from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Optional

import httpx

from app import recommendation_db as store
from app.config import settings
from app.recommendation_sources import ingest_jellyfin, ingest_kavita, ingest_navidrome
from app.tmdb import TMDBClient


USER_KEY = "sam"
_SOURCE_INGESTORS = {
    "jellyfin": ingest_jellyfin,
    "navidrome": ingest_navidrome,
    "kavita": ingest_kavita,
}


def _slug(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _safe_title(value: Optional[str], fallback: str) -> str:
    text = (value or "").strip()
    return text or fallback


def _era_for_year(year: Optional[int]) -> Optional[str]:
    if not year:
        return None
    decade = int(year / 10) * 10
    return f"{decade}s"


def rebuild_preference_signals(user_key: str = USER_KEY) -> list[dict[str, Any]]:
    activity = store.recent_activity(30, limit=500)
    now = datetime.now(UTC)
    buckets: dict[tuple[str, str], dict[str, Any]] = {}

    for row in activity:
        age_days = max((now - row["event_at"]).total_seconds() / 86400.0, 0)
        weight_7 = 3.0 if age_days <= 7 else 0.0
        weight_30 = max(0.25, 1.0 - min(age_days, 30) / 30.0)

        def add_signal(facet_type: str, facet_value: Optional[str], boost: float = 1.0) -> None:
            value = (facet_value or "").strip()
            if not value:
                return
            key = (facet_type, value)
            current = buckets.setdefault(
                key,
                {
                    "facet_type": facet_type,
                    "facet_value": value,
                    "score_7d": 0.0,
                    "score_30d": 0.0,
                    "last_seen_at": row["event_at"],
                },
            )
            current["score_7d"] += weight_7 * boost
            current["score_30d"] += weight_30 * boost
            if row["event_at"] > current["last_seen_at"]:
                current["last_seen_at"] = row["event_at"]

        add_signal("creator", row.get("primary_creator"), 1.8)
        add_signal("theme", row.get("album_or_series"), 1.4)
        for genre in _as_list(row.get("genres")):
            add_signal("genre", genre, 1.5)
        for tag in _as_list(row.get("tags")):
            add_signal("theme", tag, 1.2)
        for subject in _as_list(row.get("subjects")):
            add_signal("subject", subject, 1.5)
        add_signal("region", row.get("language"), 1.0)
        add_signal("era", _era_for_year(row.get("year")), 1.1)

    rows = list(buckets.values())
    rows.sort(key=lambda item: (item["score_7d"], item["score_30d"]), reverse=True)
    store.replace_signals(user_key, rows)
    return rows


def run_ingestion(sources: list[str], window_days: int, rebuild_signals_flag: bool = True) -> dict[str, Any]:
    run_id = store.create_run(
        "ingest",
        {
            "sources": sources,
            "window_days": window_days,
            "rebuild_signals": rebuild_signals_flag,
        },
    )
    result: dict[str, Any] = {
        "run_id": run_id,
        "sources": [],
        "signals_rebuilt": False,
    }
    try:
        for source in sources:
            ingestor = _SOURCE_INGESTORS.get(source)
            if not ingestor:
                result["sources"].append({"source": source, "status": "skipped", "reason": "unsupported"})
                continue
            try:
                outcome = ingestor(window_days)
                outcome["status"] = "ok"
            except Exception as exc:
                outcome = {"source": source, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            result["sources"].append(outcome)
        if rebuild_signals_flag:
            signals = rebuild_preference_signals(USER_KEY)
            result["signals_rebuilt"] = True
            result["signal_count"] = len(signals)
        store.finish_run(run_id, "done", result)
        return result
    except Exception as exc:
        store.finish_run(run_id, "failed", result, error_text=str(exc))
        raise


async def _lastfm_track(seed: str) -> Optional[dict[str, Any]]:
    if not settings.LASTFM_API_KEY:
        return None
    cache_key = f"lastfm:track:{_slug(seed)}"
    cached = store.cache_get(cache_key)
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://ws.audioscrobbler.com/2.0/",
            params={
                "method": "track.search",
                "track": seed,
                "api_key": settings.LASTFM_API_KEY,
                "format": "json",
                "limit": 1,
            },
        )
    if resp.status_code != 200:
        return None
    payload = resp.json()
    matches = (((payload.get("results") or {}).get("trackmatches") or {}).get("track")) or []
    if isinstance(matches, dict):
        matches = [matches]
    if not matches:
        return None
    match = matches[0]
    result = {
        "title": _safe_title(match.get("name"), seed),
        "media_type": "music_track",
        "in_library": False,
        "source_system": None,
        "candidate_source": "lastfm",
        "shared_theme": None,
        "why": f"Strong music match from Last.fm for recent activity around {seed}.",
        "acquisition_path": "music",
        "confidence_score": 0.62,
        "artist": match.get("artist"),
        "url": match.get("url"),
    }
    store.cache_put(cache_key, "lastfm", result, ttl_hours=168)
    return result


async def _openlibrary_book(seed: str) -> Optional[dict[str, Any]]:
    cache_key = f"openlibrary:book:{_slug(seed)}"
    cached = store.cache_get(cache_key)
    if cached:
        return cached
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"{settings.OPEN_LIBRARY_BASE_URL.rstrip('/')}/search.json",
            params={"title": seed, "limit": 1},
        )
    if resp.status_code != 200:
        return None
    docs = resp.json().get("docs", [])
    if not docs:
        return None
    match = docs[0]
    key = match.get("key")
    result = {
        "title": _safe_title(match.get("title"), seed),
        "media_type": "book",
        "in_library": False,
        "source_system": None,
        "candidate_source": "openlibrary",
        "shared_theme": None,
        "why": f"Related book candidate surfaced from Open Library for {seed}.",
        "acquisition_path": "librarian",
        "confidence_score": 0.61,
        "primary_creator": ", ".join(match.get("author_name", [])[:2]) if match.get("author_name") else None,
        "subjects": match.get("subject", [])[:8],
        "url": f"{settings.OPEN_LIBRARY_BASE_URL.rstrip('/')}{key}" if key else None,
    }
    store.cache_put(cache_key, "openlibrary", result, ttl_hours=168)
    return result


async def _tmdb_movie(seed: str) -> Optional[dict[str, Any]]:
    if not settings.TMDB_API_KEY:
        return None
    cache_key = f"tmdb:movie:{_slug(seed)}"
    cached = store.cache_get(cache_key)
    if cached:
        return cached
    client = TMDBClient(settings.TMDB_API_KEY)
    payload = await client.get_metadata(seed)
    if not payload:
        return None
    result = {
        "title": payload["title"],
        "media_type": "movie" if payload.get("media_type") == "movie" else "series",
        "in_library": False,
        "source_system": None,
        "candidate_source": "tmdb",
        "shared_theme": None,
        "why": f"Related screen title surfaced from TMDB for {seed}.",
        "acquisition_path": "media-assistant",
        "confidence_score": 0.64,
        "year": payload.get("year"),
        "tmdb_url": payload.get("tmdb_url"),
        "overview": payload.get("overview"),
    }
    store.cache_put(cache_key, "tmdb", result, ttl_hours=168)
    return result


async def _theme_from_candidates(candidates: list[dict[str, Any]], fallback: str) -> dict[str, str]:
    if not settings.OPENROUTER_API_KEY or not candidates:
        return {
            "theme_title": fallback,
            "theme_summary": f"This digest is anchored around {fallback.lower()} in your recent activity.",
            "llm_message_text": "",
        }
    payload = {
        "model": settings.OPENROUTER_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You generate concise cross-media theme titles and 2 sentence summaries.",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "Generate a short theme title and compact summary for these recommendation candidates.",
                        "candidates": candidates,
                    }
                ),
            },
        ],
        "temperature": 0.2,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code != 200:
        return {
            "theme_title": fallback,
            "theme_summary": f"This digest is anchored around {fallback.lower()} in your recent activity.",
            "llm_message_text": "",
        }
    content = (((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    title = fallback
    summary = f"This digest is anchored around {fallback.lower()} in your recent activity."
    if content:
        lines = [line.strip("- ").strip() for line in content.splitlines() if line.strip()]
        if lines:
            title = lines[0][:80]
        if len(lines) > 1:
            summary = " ".join(lines[1:])[:500]
        else:
            summary = content[:500]
    return {"theme_title": title, "theme_summary": summary, "llm_message_text": content}


def _score_candidate(candidate: dict[str, Any], signal_map: dict[tuple[str, str], float]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    creator = (candidate.get("primary_creator") or "").strip()
    if creator:
        creator_score = signal_map.get(("creator", creator.lower()), 0.0)
        if creator_score:
            score += creator_score * 2.0
            reasons.append(f"Recent activity is clustered around {creator}.")

    theme_value = (candidate.get("album_or_series") or "").strip()
    if theme_value:
        theme_score = signal_map.get(("theme", theme_value.lower()), 0.0)
        if theme_score:
            score += theme_score * 1.6
            reasons.append(f"It aligns with {theme_value}.")

    for genre in _as_list(candidate.get("genres")):
        genre_score = signal_map.get(("genre", genre.lower()), 0.0)
        if genre_score:
            score += genre_score * 1.4
            reasons.append(f"It matches your recent {genre} activity.")

    for subject in _as_list(candidate.get("subjects")):
        subject_score = signal_map.get(("subject", subject.lower()), 0.0)
        if subject_score:
            score += subject_score * 1.4
            reasons.append(f"It shares the subject {subject}.")

    era = _era_for_year(candidate.get("year"))
    if era:
        era_score = signal_map.get(("era", era.lower()), 0.0)
        if era_score:
            score += era_score
            reasons.append(f"It fits the {era} pattern in your recent consumption.")
    return score, reasons


def _candidate_type_map() -> dict[str, str]:
    return {
        "music": "music_track",
        "book": "book",
        "movie": "movie",
    }


def _same_domain_fallback(
    recommendation_type: str,
    candidates: list[dict[str, Any]],
    activity: list[dict[str, Any]],
    recent_titles: list[str],
) -> Optional[dict[str, Any]]:
    target_media_type = _candidate_type_map()[recommendation_type]
    domain_activity = [row for row in activity if row["media_type"] == target_media_type]
    if not domain_activity:
        return None
    seed = domain_activity[0]
    creator = (seed.get("primary_creator") or "").strip().lower()
    album_or_series = (seed.get("album_or_series") or "").strip().lower()
    filtered = [
        candidate
        for candidate in candidates
        if candidate.get("normalized_title") not in recent_titles
    ]
    if creator:
        creator_match = [
            candidate for candidate in filtered
            if (candidate.get("primary_creator") or "").strip().lower() == creator
        ]
        if creator_match:
            picked = creator_match[0]
            return {
                "title": picked["title"],
                "media_type": picked["media_type"],
                "in_library": True,
                "source_system": picked["source_system"],
                "candidate_source": "library",
                "shared_theme": None,
                "why": f"It keeps the thread of your recent {seed['primary_creator']} listening.",
                "acquisition_path": "none",
                "confidence_score": 0.72,
                "primary_creator": picked.get("primary_creator"),
                "album_or_series": picked.get("album_or_series"),
                "year": picked.get("year"),
                "library_path": picked.get("library_path"),
            }
    if album_or_series:
        album_match = [
            candidate for candidate in filtered
            if (candidate.get("album_or_series") or "").strip().lower() == album_or_series
        ]
        if album_match:
            picked = album_match[0]
            return {
                "title": picked["title"],
                "media_type": picked["media_type"],
                "in_library": True,
                "source_system": picked["source_system"],
                "candidate_source": "library",
                "shared_theme": None,
                "why": f"It stays close to your recent {seed['album_or_series']} activity.",
                "acquisition_path": "none",
                "confidence_score": 0.68,
                "primary_creator": picked.get("primary_creator"),
                "album_or_series": picked.get("album_or_series"),
                "year": picked.get("year"),
                "library_path": picked.get("library_path"),
            }
    return None


async def _best_external(seed: str, recommendation_type: str) -> Optional[dict[str, Any]]:
    if recommendation_type == "music":
        return await _lastfm_track(seed)
    if recommendation_type == "book":
        return await _openlibrary_book(seed)
    return await _tmdb_movie(seed)


def _fallback_theme(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return "Fresh Discovery"
    top = signals[0]
    return f"{top['facet_value']} Discovery"


async def generate_recommendations(
    *,
    mode: str,
    lookback_days: int,
    library_only: bool,
    include_new_finds: bool,
    limit_per_type: int,
) -> dict[str, Any]:
    run_id = store.create_run(
        "generate",
        {
            "mode": mode,
            "lookback_days": lookback_days,
            "library_only": library_only,
            "include_new_finds": include_new_finds,
            "limit_per_type": limit_per_type,
        },
    )
    activity = store.recent_activity(lookback_days, limit=200)
    signals = store.top_signals(USER_KEY, limit=24)
    if not signals:
        signals = rebuild_preference_signals(USER_KEY)
    signal_map = {
        (row["facet_type"].lower(), row["facet_value"].lower()): float(row["score_7d"]) + float(row["score_30d"])
        for row in signals
    }
    recent_titles = list({row["normalized_title"] for row in activity if row.get("normalized_title")})
    supporting_activity = [
        {
            "title": row["title"],
            "media_type": row["media_type"],
            "event_type": row["event_type"],
            "event_at": row["event_at"].isoformat(),
            "primary_creator": row.get("primary_creator"),
        }
        for row in activity[:12]
    ]

    output: dict[str, Any] = {
        "run_id": run_id,
        "mode": mode,
        "generated_at": datetime.now(UTC).isoformat(),
        "supporting_activity": supporting_activity,
        "recommendations": [],
    }
    chosen: list[dict[str, Any]] = []

    for recommendation_type, media_type in _candidate_type_map().items():
        candidates = store.library_candidates(media_type, recent_titles, limit=150)
        ranked: list[tuple[float, dict[str, Any], list[str]]] = []
        for candidate in candidates:
            score, reasons = _score_candidate(candidate, signal_map)
            if score <= 0:
                continue
            ranked.append((score, candidate, reasons))
        ranked.sort(key=lambda item: item[0], reverse=True)

        selected: Optional[dict[str, Any]] = None
        if ranked:
            score, candidate, reasons = ranked[0]
            selected = {
                "title": candidate["title"],
                "media_type": candidate["media_type"],
                "in_library": True,
                "source_system": candidate["source_system"],
                "candidate_source": "library",
                "shared_theme": None,
                "why": " ".join(dict.fromkeys(reasons)) or "Matches your recent consumption pattern.",
                "acquisition_path": "none",
                "confidence_score": round(min(score / 10.0, 0.99), 3),
                "primary_creator": candidate.get("primary_creator"),
                "album_or_series": candidate.get("album_or_series"),
                "year": candidate.get("year"),
                "library_path": candidate.get("library_path"),
            }
        if selected is None:
            selected = _same_domain_fallback(recommendation_type, candidates, activity, recent_titles)

        if (selected is None or (include_new_finds and not library_only and selected["confidence_score"] < 0.45)):
            seed = signals[0]["facet_value"] if signals else (activity[0]["title"] if activity else recommendation_type)
            external = await _best_external(seed, recommendation_type)
            if external and (selected is None or external["confidence_score"] > selected["confidence_score"]):
                selected = external

        if selected is None:
            selected = {
                "title": f"No {recommendation_type} candidate found",
                "media_type": media_type,
                "in_library": False,
                "source_system": None,
                "candidate_source": "none",
                "shared_theme": None,
                "why": "No strong candidate was available for this type yet.",
                "acquisition_path": "none",
                "confidence_score": 0.0,
            }

        chosen.append(selected)
        store.insert_candidate(run_id, recommendation_type, selected)
        output["recommendations"].append({"type": recommendation_type, **selected})

    theme = await _theme_from_candidates(chosen, _fallback_theme(signals))
    for item in output["recommendations"]:
        item["shared_theme"] = theme["theme_title"]

    output["theme_title"] = theme["theme_title"]
    output["theme_summary"] = theme["theme_summary"]
    output["llm_message_text"] = theme.get("llm_message_text") or ""

    if mode == "weekly":
        store.insert_weekly_digest(
            run_id=run_id,
            theme_title=theme["theme_title"],
            theme_summary=theme["theme_summary"],
            music_item=next(item for item in output["recommendations"] if item["type"] == "music"),
            book_item=next(item for item in output["recommendations"] if item["type"] == "book"),
            movie_item=next(item for item in output["recommendations"] if item["type"] == "movie"),
            supporting_activity=supporting_activity,
            llm_summary=theme["theme_summary"],
            llm_message_text=theme.get("llm_message_text") or "",
        )

    store.finish_run(run_id, "done", output)
    return output


async def query_recommendations(
    *,
    seed_type: str,
    seed_value: str,
    include_library: bool,
    include_external: bool,
) -> dict[str, Any]:
    local = store.search_media(seed_value, limit=25) if include_library else []
    response: dict[str, Any] = {
        "seed_type": seed_type,
        "seed_value": seed_value,
        "library_matches": [],
        "external_matches": [],
    }
    for item in local[:10]:
        response["library_matches"].append(
            {
                "title": item["title"],
                "media_type": item["media_type"],
                "in_library": True,
                "source_system": item["source_system"],
                "candidate_source": "library",
                "shared_theme": seed_value,
                "why": f"Local library match for {seed_value}.",
                "acquisition_path": "none",
                "confidence_score": 0.7,
                "primary_creator": item.get("primary_creator"),
                "album_or_series": item.get("album_or_series"),
                "year": item.get("year"),
            }
        )
    if include_external:
        movie = await _tmdb_movie(seed_value)
        book = await _openlibrary_book(seed_value)
        music = await _lastfm_track(seed_value)
        response["external_matches"] = [item for item in [movie, book, music] if item]
        for item in response["external_matches"]:
            item["shared_theme"] = seed_value
    return response


def ingest_status() -> dict[str, Any]:
    latest = store.latest_run("ingest")
    return {
        "enabled": settings.RECOMMENDATIONS_ENABLED,
        "latest_run": latest,
    }


def latest_recommendation_digest() -> Optional[dict[str, Any]]:
    return store.latest_digest()


def recommendation_history(limit: int = 20) -> list[dict[str, Any]]:
    return store.digest_history(limit=limit)
