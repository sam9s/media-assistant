from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import settings
from app import recommendation_db as store


def _normalize_title(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _json_listish(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            value = value.hex()
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if "|" in text:
        parts = text.split("|")
    elif ";" in text:
        parts = text.split(";")
    elif "," in text:
        parts = text.split(",")
    else:
        parts = [text]
    return [p.strip() for p in parts if p.strip()]


def _to_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _safe_jsonish(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _safe_jsonish(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_jsonish(v) for v in value]
    return value


def _open_sqlite(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ingest_navidrome(window_days: int) -> dict[str, Any]:
    path = settings.RECOMMENDATIONS_SOURCE_NAVIDROME_DB
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    count = 0
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Navidrome DB not found: {path}")

    library_count = 0
    with _open_sqlite(path) as conn:
        library_rows = conn.execute(
            """
            select id, path, title, album, artist, album_artist, year, genre
            from media_file
            """
        ).fetchall()
        rows = conn.execute(
            """
            select
                a.user_id,
                a.item_id,
                a.play_count,
                a.play_date,
                a.rating,
                a.starred,
                m.id as media_id,
                m.path,
                m.title,
                m.album,
                m.artist,
                m.album_artist,
                m.year,
                m.genre
            from annotation a
            join media_file m on m.id = a.item_id
            where a.item_type = 'media_file'
              and a.play_date is not null
            order by a.play_date desc
            """
        ).fetchall()

    for row in library_rows:
        store.upsert_media_item(
            media_type="music_track",
            source_system="navidrome",
            source_item_id=str(row["id"]),
            title=row["title"] or Path(row["path"] or "").stem or "Unknown Track",
            normalized_title=_normalize_title(row["title"] or Path(row["path"] or "").stem or "Unknown Track"),
            primary_creator=row["artist"] or row["album_artist"],
            contributors=_json_listish(row["artist"]),
            album_or_series=row["album"],
            year=row["year"],
            genres=_json_listish(row["genre"]),
            tags=[],
            subjects=[],
            language=None,
            library_path=row["path"],
            in_library=True,
            canonical_external_ids={},
            metadata={"album_artist": row["album_artist"]},
        )
        library_count += 1

    for row in rows:
        event_at = _to_dt(row["play_date"])
        if not event_at or event_at < cutoff:
            continue
        media_id = store.upsert_media_item(
            media_type="music_track",
            source_system="navidrome",
            source_item_id=str(row["media_id"]),
            title=row["title"] or Path(row["path"] or "").stem or "Unknown Track",
            normalized_title=_normalize_title(row["title"] or Path(row["path"] or "").stem or "Unknown Track"),
            primary_creator=row["artist"] or row["album_artist"],
            contributors=_json_listish(row["artist"]),
            album_or_series=row["album"],
            year=row["year"],
            genres=_json_listish(row["genre"]),
            tags=[],
            subjects=[],
            language=None,
            library_path=row["path"],
            in_library=True,
            canonical_external_ids={},
            metadata={
                "user_id": row["user_id"],
                "rating": row["rating"],
                "starred": row["starred"],
                "album_artist": row["album_artist"],
            },
        )
        store.insert_activity_event(
            source_system="navidrome",
            source_event_id=f"{row['user_id']}:{row['media_id']}:{row['play_date']}",
            media_item_id=media_id,
            event_type="played",
            event_at=event_at,
            completion_ratio=1.0,
            play_count_delta=int(row["play_count"] or 1),
            progress_percent=100.0,
            raw_payload_json=_safe_jsonish(dict(row)),
        )
        count += 1
    return {"source": "navidrome", "events_ingested": count, "library_items_seeded": library_count}


def ingest_kavita(window_days: int) -> dict[str, Any]:
    path = settings.RECOMMENDATIONS_SOURCE_KAVITA_DB
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    count = 0
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Kavita DB not found: {path}")

    with _open_sqlite(path) as conn:
        series_rows = conn.execute(
            """
            select
                s.Id as series_id,
                s.Name as series_name,
                s.LocalizedName as series_localized_name,
                s.Format,
                s.LibraryId,
                sm.ReleaseYear,
                sm.Language
            from Series s
            left join SeriesMetadata sm on sm.SeriesId = s.Id
            """
        ).fetchall()
        rows = conn.execute(
            """
            select
                p.Id as progress_id,
                p.AppUserId,
                p.CreatedUtc,
                p.LastModifiedUtc,
                p.PagesRead,
                p.SeriesId,
                p.VolumeId,
                p.ChapterId,
                s.Name as series_name,
                s.LocalizedName as series_localized_name,
                s.Format,
                s.LibraryId,
                c.Title as chapter_title,
                c.Number as chapter_number,
                v.Name as volume_name
            from AppUserProgresses p
            left join Series s on s.Id = p.SeriesId
            left join Chapter c on c.Id = p.ChapterId
            left join Volume v on v.Id = p.VolumeId
            order by p.LastModifiedUtc desc
            """
        ).fetchall()

        people_by_series: dict[int, list[str]] = {}
        for person in conn.execute(
            """
            select distinct sm.SeriesId, pe.Name
            from SeriesMetadataPeople sp
                join Person pe on pe.Id = sp.PersonId
                join SeriesMetadata sm on sm.Id = sp.SeriesMetadataId
            """
        ).fetchall():
            people_by_series.setdefault(int(person["SeriesId"]), []).append(person["Name"])

        genres_by_series: dict[int, list[str]] = {}
        try:
            genre_rows = conn.execute(
                """
                select distinct sm.SeriesId, g.Title
                from SeriesMetadataGenres sg
                join Genre g on g.Id = sg.GenreId
                join SeriesMetadata sm on sm.Id = sg.SeriesMetadataId
                """
            ).fetchall()
        except sqlite3.OperationalError:
            genre_rows = []
        for genre in genre_rows:
            genres_by_series.setdefault(int(genre["SeriesId"]), []).append(genre["Title"])

    library_count = 0
    for row in series_rows:
        series_id = int(row["series_id"])
        title = row["series_name"] or row["series_localized_name"] or "Unknown Book"
        contributors = people_by_series.get(series_id, [])
        store.upsert_media_item(
            media_type="book",
            source_system="kavita",
            source_item_id=f"series:{series_id}",
            title=title,
            normalized_title=_normalize_title(title),
            primary_creator=contributors[0] if contributors else None,
            contributors=contributors,
            album_or_series=title,
            year=row["ReleaseYear"] or None,
            genres=genres_by_series.get(series_id, []),
            tags=[row["Format"]] if row["Format"] else [],
            subjects=[],
            language=row["Language"],
            library_path=None,
            in_library=True,
            canonical_external_ids={},
            metadata={"library_id": row["LibraryId"]},
        )
        library_count += 1

    for row in rows:
        event_at = _to_dt(row["LastModifiedUtc"] or row["CreatedUtc"])
        if not event_at or event_at < cutoff:
            continue
        series_id = int(row["SeriesId"]) if row["SeriesId"] is not None else 0
        title = row["series_name"] or row["series_localized_name"] or row["chapter_title"] or "Unknown Book"
        contributors = people_by_series.get(series_id, [])
        media_id = store.upsert_media_item(
            media_type="book",
            source_system="kavita",
            source_item_id=f"series:{series_id}" if series_id else f"progress:{row['progress_id']}",
            title=title,
            normalized_title=_normalize_title(title),
            primary_creator=contributors[0] if contributors else None,
            contributors=contributors,
            album_or_series=row["series_name"],
            year=None,
            genres=genres_by_series.get(series_id, []),
            tags=[row["Format"]] if row["Format"] else [],
            subjects=[],
            language=None,
            library_path=None,
            in_library=True,
            canonical_external_ids={},
            metadata={
                "app_user_id": row["AppUserId"],
                "chapter_title": row["chapter_title"],
                "chapter_number": row["chapter_number"],
                "volume_name": row["volume_name"],
                "library_id": row["LibraryId"],
            },
        )
        pages = float(row["PagesRead"] or 0)
        store.insert_activity_event(
            source_system="kavita",
            source_event_id=f"{row['AppUserId']}:{row['progress_id']}:{row['LastModifiedUtc'] or row['CreatedUtc']}",
            media_item_id=media_id,
            event_type="reading_progress",
            event_at=event_at,
            completion_ratio=None,
            play_count_delta=None,
            progress_percent=pages,
            raw_payload_json=_safe_jsonish(dict(row)),
        )
        count += 1
    return {"source": "kavita", "events_ingested": count, "library_items_seeded": library_count}


def ingest_jellyfin(window_days: int) -> dict[str, Any]:
    path = settings.RECOMMENDATIONS_SOURCE_JELLYFIN_DB
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    count = 0
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Jellyfin DB not found: {path}")

    with _open_sqlite(path) as conn:
        rows = conn.execute(
            """
            select
                u.userId,
                u.key as userdata_key,
                u.played,
                u.playCount,
                u.playbackPositionTicks,
                u.lastPlayedDate,
                t.guid,
                t.UserDataKey,
                t.Name,
                t.MediaType,
                t.type,
                t.Overview,
                t.ProductionYear,
                t.Path,
                t.Genres,
                t.Tags,
                t.SeriesName,
                t.Album,
                t.Artists,
                t.AlbumArtists,
                t.ProviderIds
            from UserDatas u
            left join TypedBaseItems t on t.UserDataKey = u.key or t.guid = u.key
            where u.lastPlayedDate is not null
            order by u.lastPlayedDate desc
            """
        ).fetchall()

    for row in rows:
        event_at = _to_dt(row["lastPlayedDate"])
        if not event_at or event_at < cutoff:
            continue
        item_type = (row["type"] or "").lower()
        media_type = (row["MediaType"] or "").lower()
        if not row["Name"] and not row["Path"]:
            continue
        reco_type = "movie"
        event_type = "watched"
        if "episode" in item_type or media_type == "episode":
            reco_type = "series"
        elif media_type == "audio" or "audio" in item_type:
            reco_type = "music_track"
            event_type = "played"
        title = row["Name"] or row["SeriesName"] or row["Album"] or "Unknown Media"
        guid = row["guid"].hex() if isinstance(row["guid"], bytes) else row["guid"]
        provider_ids = {}
        for part in _json_listish(row["ProviderIds"]):
            if "=" in part:
                key, value = part.split("=", 1)
                provider_ids[key.strip().lower()] = value.strip()
        media_id = store.upsert_media_item(
            media_type=reco_type,
            source_system="jellyfin",
            source_item_id=str(guid or row["userdata_key"]),
            title=title,
            normalized_title=_normalize_title(title),
            primary_creator=(_json_listish(row["Artists"]) or _json_listish(row["AlbumArtists"]) or [None])[0],
            contributors=_json_listish(row["Artists"]) or _json_listish(row["AlbumArtists"]),
            album_or_series=row["SeriesName"] or row["Album"],
            year=row["ProductionYear"],
            genres=_json_listish(row["Genres"]),
            tags=_json_listish(row["Tags"]),
            subjects=[],
            language=None,
            library_path=row["Path"],
            in_library=True,
            canonical_external_ids=provider_ids,
            metadata={
                "overview": row["Overview"],
                "playback_position_ticks": row["playbackPositionTicks"],
                "play_count": row["playCount"],
                "played": row["played"],
            },
        )
        progress_percent = None
        if row["playbackPositionTicks"]:
            progress_percent = float(row["playbackPositionTicks"]) / 10_000_000.0
        store.insert_activity_event(
            source_system="jellyfin",
            source_event_id=f"{row['userId']}:{row['userdata_key']}:{row['lastPlayedDate']}",
            media_item_id=media_id,
            event_type=event_type,
            event_at=event_at,
            completion_ratio=1.0 if row["played"] else None,
            play_count_delta=int(row["playCount"] or 1),
            progress_percent=progress_percent,
            raw_payload_json=_safe_jsonish(dict(row)),
        )
        count += 1
    return {"source": "jellyfin", "events_ingested": count}
