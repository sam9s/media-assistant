import asyncio
import base64
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

import musicbrainzngs
from mutagen.flac import Picture
from mutagen.oggopus import OggOpus

from app.config import settings
from app.music_enrichment import (
    _fetch_bytes,
    _mb_release_from_recording,
    _theaudiodb_cover,
    _theaudiodb_track_cover,
)
from app.tmdb import TMDBClient

logger = logging.getLogger("uvicorn.error")

musicbrainzngs.set_useragent("SamAssist", "3.0", "sam@sam9scloud.in")
_tmdb = TMDBClient(api_key=settings.TMDB_API_KEY)

_CHANNEL_WORDS = {
    "official", "records", "music", "topic", "channel", "audio", "video",
    "lyrics", "lyrical", "films", "movies", "hd", "4k",
}


def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("｜", "|")
    text = text.replace("–", "-")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _cleanup_title(text: str) -> str:
    text = (text or "").replace("｜", "|").replace("–", "-")
    text = re.sub(r"\[(official|lyrics?|lyric video|video|audio|hd|4k).*?\]", " ", text, flags=re.I)
    text = re.sub(r"\((official|lyrics?|lyric video|video|audio|hd|4k|full song).*?\)", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -|")
    return text.strip()


def _cleanup_track_name(text: str) -> str:
    text = _cleanup_title(text)
    text = re.sub(r"\([^)]*\)", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -|")


def _looks_like_channel(name: str) -> bool:
    norm = _normalize(name)
    if not norm:
        return True
    return any(word in norm.split() for word in _CHANNEL_WORDS)


def _extract_year(text: str) -> str:
    m = re.search(r"\b(19|20)\d{2}\b", text or "")
    return m.group(0) if m else ""


def _artist_from_context(text: str) -> str:
    text = (text or "").replace("｜", "|")
    year_match = re.search(r"\b(?:19|20)\d{2}\b", text)
    if year_match:
        text = text[year_match.end():]
    text = re.sub(r"\([^)]*\)", " ", text)
    parts = [p.strip() for p in text.split("/") if p.strip()]
    if parts:
        return parts[0]
    bits = [p.strip() for p in text.split("-") if p.strip()]
    return bits[-1] if bits else ""


def _album_from_context(text: str) -> str:
    text = (text or "").replace("｜", "|")
    year_match = re.search(r"\b(?:19|20)\d{2}\b", text)
    if year_match:
        text = text[:year_match.start()]
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[()]+", " ", text)
    bits = [p.strip() for p in text.split("/") if p.strip()]
    if bits:
        return bits[0].strip(" -|")
    bits = [p.strip() for p in text.split("-") if p.strip()]
    return bits[0].strip(" -|") if bits else ""


def _looks_like_soundtrack(raw_title: str, album: str, year: str) -> bool:
    haystack = _normalize(" ".join(filter(None, [raw_title, album, year])))
    if year:
        return True
    soundtrack_markers = {"ost", "soundtrack", "film", "movie"}
    if any(marker in haystack.split() for marker in soundtrack_markers):
        return True
    return bool(album and raw_title and _normalize(album) in haystack)


def _build_candidates(raw_title: str, raw_uploader: str) -> list[dict]:
    candidates: list[dict] = []
    cleaned_full = _cleanup_title(raw_title)
    cleaned_track = _cleanup_track_name(raw_title)
    year = _extract_year(raw_title)

    def add(title: str, artist: str, album: str = "") -> None:
        title = title.strip()
        artist = artist.strip()
        album = album.strip()
        key = (_normalize(title), _normalize(artist), _normalize(album))
        if title and key not in {(c["key"]) for c in candidates}:
            candidates.append({
                "title": title,
                "artist": artist,
                "album": album,
                "year": year,
                "key": key,
            })

    if " - " in raw_title:
        lead, tail = raw_title.split(" - ", 1)
        add(_cleanup_track_name(lead), _artist_from_context(tail), _cleanup_title(tail))
        if not _looks_like_channel(raw_uploader):
            add(_cleanup_track_name(lead), _cleanup_title(raw_uploader), _cleanup_title(tail))

    if not _looks_like_channel(raw_uploader):
        add(cleaned_track, _cleanup_title(raw_uploader))

    add(cleaned_track, "")
    add(cleaned_full, "")
    return candidates


def _best_recording_match(candidates: list[dict]) -> Optional[dict]:
    best: Optional[dict] = None

    for candidate in candidates:
        kwargs = {"recording": candidate["title"], "limit": 5}
        if candidate["artist"]:
            kwargs["artist"] = candidate["artist"]
        try:
            result = musicbrainzngs.search_recordings(**kwargs)
        except Exception as e:
            logger.warning("MusicBrainz search failed for %s: %s", candidate, e)
            continue

        for rec in result.get("recording-list", []):
            artist = ""
            credit = rec.get("artist-credit", [])
            if credit and isinstance(credit[0], dict):
                artist = credit[0].get("artist", {}).get("name", "")

            mb_score = float(rec.get("ext:score", 0)) / 100.0
            title_sim = SequenceMatcher(None, _normalize(candidate["title"]), _normalize(rec.get("title", ""))).ratio()
            artist_sim = 0.0
            if candidate["artist"]:
                artist_sim = SequenceMatcher(None, _normalize(candidate["artist"]), _normalize(artist)).ratio()
            elif artist:
                artist_sim = 0.5

            score = (mb_score * 0.55) + (title_sim * 0.35) + (artist_sim * 0.10)
            first_release = rec.get("first-release-date", "")
            if candidate["year"] and first_release.startswith(candidate["year"]):
                score += 0.05

            if not best or score > best["score"]:
                best = {
                    "score": score,
                    "recording_id": rec.get("id"),
                    "title": rec.get("title", ""),
                    "artist": artist,
                }

    if best and best["score"] >= 0.78:
        return best
    return None


def _mb_release_cover_ids(recording_id: str) -> dict:
    """Return best available MusicBrainz release and release-group ids for cover art."""
    try:
        result = musicbrainzngs.get_recording_by_id(
            recording_id,
            includes=["releases"],
        )
        recording = result.get("recording", {})
        releases = recording.get("release-list", []) or []
        if not releases:
            return {}
        rel = releases[0]
        release_group = rel.get("release-group", {}) or {}
        return {
            "release_id": rel.get("id"),
            "release_group_id": release_group.get("id"),
        }
    except Exception as e:
        logger.warning("MusicBrainz release-id lookup failed for %s: %s", recording_id, e)
        return {}


async def _cover_art_archive_cover(release_id: str = "", release_group_id: str = "") -> tuple[Optional[bytes], Optional[str]]:
    candidates = []
    if release_id:
        candidates.append(("coverartarchive-release", f"https://coverartarchive.org/release/{release_id}/front-500"))
        candidates.append(("coverartarchive-release", f"https://coverartarchive.org/release/{release_id}/front"))
    if release_group_id:
        candidates.append(("coverartarchive-release-group", f"https://coverartarchive.org/release-group/{release_group_id}/front-500"))
        candidates.append(("coverartarchive-release-group", f"https://coverartarchive.org/release-group/{release_group_id}/front"))
    for source, url in candidates:
        data = await _fetch_bytes(url)
        if data:
            return data, source
    return None, None


async def _tmdb_cover(raw_title: str, album: str, year: str) -> tuple[Optional[bytes], Optional[str]]:
    if not settings.TMDB_API_KEY:
        return None, None
    year_int = int(year) if year and year.isdigit() else None
    queries = []
    if album:
        queries.append(album)
    if raw_title and album and raw_title != album:
        queries.append(album)
    if raw_title:
        queries.append(_album_from_context(raw_title) or raw_title)

    seen = set()
    for query in queries:
        q = query.strip()
        if not q or q in seen:
            continue
        seen.add(q)
        meta = await _tmdb.get_movie_poster(q, year=year_int)
        if year_int is not None and meta and meta.get("year") != year_int:
            continue
        poster_url = (meta or {}).get("poster_url")
        if poster_url:
            data = await _fetch_bytes(poster_url)
            if data:
                return data, "tmdb"
    return None, None


def _opus_write_tags(path: str, tags: dict, cover_bytes: Optional[bytes]) -> None:
    audio = OggOpus(path)
    for key, value in tags.items():
        if value:
            audio[key] = [str(value)]
    if cover_bytes:
        pic = Picture()
        pic.type = 3
        pic.data = cover_bytes
        pic.mime = "image/png" if cover_bytes.startswith(b"\x89PNG") else "image/jpeg"
        audio["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
    audio.save()


def _opus_has_cover(path: str) -> bool:
    audio = OggOpus(path)
    pics = audio.get("metadata_block_picture", [])
    return bool(pics)


async def enrich_youtube_opus(path: str, raw_title: str, raw_uploader: str, source_url: str = "") -> dict:
    result = {
        "enrichment_status": "skipped",
        "enrichment_source": None,
        "enriched_title": None,
        "enriched_artist": None,
        "enriched_album": None,
        "cover_art_applied": False,
        "cover_art_source": None,
    }

    try:
        candidates = _build_candidates(raw_title, raw_uploader)
        best = await asyncio.to_thread(_best_recording_match, candidates)

        artist = ""
        title = _cleanup_track_name(raw_title) or raw_title
        album = title
        year = _extract_year(raw_title)
        source = "fallback"
        parsed_album = ""
        if " - " in raw_title:
            parsed_album = _album_from_context(raw_title.split(" - ", 1)[1])

        if best:
            meta = await asyncio.to_thread(_mb_release_from_recording, best["recording_id"])
            artist = (meta or {}).get("artist") or best["artist"] or artist
            title = best["title"] or title
            album = (meta or {}).get("album") or parsed_album or album
            year = (meta or {}).get("year") or year
            source = "musicbrainz"
        else:
            parsed_artist = ""
            if " - " in raw_title:
                parsed_artist = _artist_from_context(raw_title.split(" - ", 1)[1])
            if not parsed_artist and not _looks_like_channel(raw_uploader):
                parsed_artist = _cleanup_title(raw_uploader)
            artist = parsed_artist or raw_uploader

        cover_bytes = None
        cover_source = None
        if best:
            ids = await asyncio.to_thread(_mb_release_cover_ids, best["recording_id"])
            cover_bytes, cover_source = await _cover_art_archive_cover(
                release_id=ids.get("release_id", ""),
                release_group_id=ids.get("release_group_id", ""),
            )
        if not cover_bytes and _looks_like_soundtrack(raw_title, album, year):
            cover_bytes, cover_source = await _tmdb_cover(raw_title, album, year)
        if not cover_bytes:
            cover_url = await _theaudiodb_cover(artist, album) if artist and album else None
            if not cover_url and artist and title:
                cover_url = await _theaudiodb_track_cover(artist, title)
            cover_bytes = await _fetch_bytes(cover_url) if cover_url else None
            if cover_bytes:
                cover_source = "theaudiodb"

        tags = {
            "title": title,
            "artist": artist,
            "album": album or title,
            "albumartist": artist,
            "date": year,
            "comment": source_url,
        }
        await asyncio.to_thread(_opus_write_tags, path, tags, cover_bytes)
        has_cover = await asyncio.to_thread(_opus_has_cover, path)

        result.update({
            "enrichment_status": "applied",
            "enrichment_source": source,
            "enriched_title": title,
            "enriched_artist": artist,
            "enriched_album": album or title,
            "cover_art_applied": has_cover,
            "cover_art_source": cover_source if cover_bytes else ("existing-embedded" if has_cover else None),
        })
    except Exception as e:
        logger.warning("YouTube enrichment failed for %s: %s", path, e)
        result["enrichment_status"] = "failed"

    return result
