import logging

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
