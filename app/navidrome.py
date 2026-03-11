import asyncio
import logging
import sqlite3
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger("uvicorn.error")

# Subsonic API v1 params reused in every call
_BASE_PARAMS = {
    "v": "1.16.1",
    "c": "SamAssist",
    "f": "json",
}


def _params(**extra) -> dict:
    return {
        **_BASE_PARAMS,
        "u": settings.NAVIDROME_USERNAME,
        "p": settings.NAVIDROME_PASSWORD,
        **extra,
    }


async def search_album(artist: str, album: str) -> bool:
    """Return True if this artist+album already exists in Navidrome."""
    if not settings.NAVIDROME_USERNAME:
        return False
    try:
        query = f"{artist} {album}".strip()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{settings.NAVIDROME_URL}/rest/search3.view",
                params=_params(query=query, albumCount=10, artistCount=0, songCount=0),
            )
        data = r.json().get("subsonic-response", {})
        albums = data.get("searchResult3", {}).get("album", [])
        album_lower = album.lower()
        artist_lower = artist.lower()
        for a in albums:
            if album_lower in a.get("name", "").lower() and artist_lower in a.get("artist", "").lower():
                return True
        return False
    except Exception as e:
        logger.warning("Navidrome search error: %s", e)
        return False


async def trigger_scan() -> dict:
    """Ask Navidrome to rescan the music library. Returns scan status dict."""
    if not settings.NAVIDROME_USERNAME:
        return {"scan_triggered": False, "scan_error": "NAVIDROME_USERNAME not set"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{settings.NAVIDROME_URL}/rest/startScan.view",
                params=_params(),
            )
        resp = r.json().get("subsonic-response", {})
        ok = resp.get("status") == "ok"
        return {"scan_triggered": ok, "scan_error": None if ok else str(resp)}
    except Exception as e:
        logger.warning("Navidrome scan trigger error: %s", e)
        return {"scan_triggered": False, "scan_error": str(e)}


def _navidrome_db_path() -> str:
    return settings.RECOMMENDATIONS_SOURCE_NAVIDROME_DB


def _to_navidrome_rel_path(path: str) -> str | None:
    if not path:
        return None
    text = path.replace("\\", "/")
    marker = "/mnt/cloud/gdrive/Media/Music/"
    if marker in text:
        return text.split(marker, 1)[1]
    return None


def _detect_stale_entries_sync(old_paths: list[str]) -> dict:
    rel_paths = [p for p in (_to_navidrome_rel_path(p) for p in old_paths) if p]
    if not rel_paths:
        return {"stale_found": False, "stale_media": [], "orphan_albums": [], "orphan_artists": []}

    db_path = _navidrome_db_path()
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    placeholders = ",".join("?" for _ in rel_paths)
    cur.execute(
        f"""
        SELECT id, path, title, album, artist, album_id, artist_id, album_artist_id, missing
        FROM media_file
        WHERE path IN ({placeholders})
        ORDER BY path
        """,
        rel_paths,
    )
    media_rows = [dict(r) for r in cur.fetchall()]

    stale_media = [r for r in media_rows if int(r.get("missing") or 0) == 1]
    album_ids = sorted({r["album_id"] for r in stale_media if r.get("album_id")})
    artist_ids = sorted(
        {
            x
            for r in stale_media
            for x in (r.get("artist_id"), r.get("album_artist_id"))
            if x
        }
    )

    orphan_albums: list[dict] = []
    for album_id in album_ids:
        cur.execute(
            """
            SELECT id, name, album_artist, song_count, size, missing
            FROM album
            WHERE id=?
            """,
            (album_id,),
        )
        row = cur.fetchone()
        if not row:
            continue
        cur.execute("SELECT COUNT(*) FROM media_file WHERE album_id=?", (album_id,))
        media_count = cur.fetchone()[0]
        if media_count == 0:
            orphan_albums.append(dict(row))

    orphan_artists: list[dict] = []
    for artist_id in artist_ids:
        cur.execute("SELECT id, name, missing FROM artist WHERE id=?", (artist_id,))
        row = cur.fetchone()
        if not row:
            continue
        cur.execute(
            "SELECT COUNT(*) FROM media_file WHERE artist_id=? OR album_artist_id=?",
            (artist_id, artist_id),
        )
        media_count = cur.fetchone()[0]
        if media_count == 0:
            orphan_artists.append(dict(row))

    conn.close()
    return {
        "stale_found": bool(stale_media or orphan_albums or orphan_artists),
        "stale_media": stale_media,
        "orphan_albums": orphan_albums,
        "orphan_artists": orphan_artists,
    }


async def detect_stale_entries(old_paths: list[str]) -> dict:
    return await asyncio.to_thread(_detect_stale_entries_sync, old_paths)
