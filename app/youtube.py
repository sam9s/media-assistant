"""
YouTube Opus Maven — search YouTube / playlists, download audio in Opus 256kbps.

Endpoints:
  POST /youtube/search   — search YouTube (+ optional playlist check)
  POST /youtube/download — trigger background download
  GET  /youtube/status/{id} — poll download status
"""
import asyncio
import logging
import time
import uuid
from asyncio.subprocess import PIPE

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from app import navidrome
from app.config import settings

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/youtube", tags=["youtube"])

# ---------------------------------------------------------------------------
# Auth (same pattern as main.py / music.py)
# ---------------------------------------------------------------------------
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: str = Security(_api_key_header)) -> str:
    if key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key


# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
_yt_search_cache: dict[str, dict] = {}   # search_id → {results: [...]}
_yt_downloads: dict[str, dict] = {}       # download_id → {status, title, language, error}
_playlist_cache: dict[str, dict] = {}     # url → {items: [...], expires: float}

_PLAYLIST_TTL = 3600  # cache playlist contents for 1 hour

# Destination root per language
_DEST: dict[str, str] = {
    "english":  "/mnt/cloud/gdrive/Media/Music/English/YouTube_Music",
    "hindi":    "/mnt/cloud/gdrive/Media/Music/Hindi/YouTube_Music",
    "punjabi":  "/mnt/cloud/gdrive/Media/Music/Punjabi/YouTube_Music",
}


def _validate_cookies_file(path: str) -> tuple[bool, str]:
    import os

    if not path:
        return False, "path missing"
    if not os.path.isfile(path):
        return False, "file missing"
    if os.path.getsize(path) <= 0:
        return False, "file empty"
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
    except Exception as e:
        return False, f"read error: {e}"
    if "Netscape HTTP Cookie File" not in first:
        return False, "invalid format header"
    return True, "ok"


# ---------------------------------------------------------------------------
# Playlist helpers
# ---------------------------------------------------------------------------

def _ydl_opts_base() -> dict:
    """Base yt-dlp Python API options. Uses cookies file if present (for YT Premium quality)."""
    opts: dict = {"quiet": True, "no_warnings": True}
    cookies = settings.YOUTUBE_COOKIES_FILE
    valid, _reason = _validate_cookies_file(cookies)
    if valid:
        opts["cookiefile"] = cookies
    return opts


async def _fetch_playlist(url: str) -> list[dict]:
    """Extract flat playlist items from a single URL. Runs in executor to avoid blocking."""
    import yt_dlp  # imported here so startup is not blocked if package is absent

    def _extract() -> list[dict]:
        opts = _ydl_opts_base()
        opts["extract_flat"] = True
        opts["playlistend"] = 2000  # safety cap
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            playlist_name = info.get("title") or info.get("playlist_title") or "Playlist"
            entries = info.get("entries") or []
            items = []
            for e in entries:
                vid = e.get("id") or e.get("url", "")
                if not vid:
                    continue
                items.append({
                    "video_id":     vid,
                    "title":        e.get("title") or "",
                    "uploader":     e.get("uploader") or e.get("channel") or "",
                    "url":          f"https://www.youtube.com/watch?v={vid}",
                    "playlist_name": playlist_name,
                })
            return items

    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _extract),
            timeout=30,
        )
    except Exception as e:
        logger.warning("Playlist fetch failed for %s: %s", url, e)
        return []


async def _load_playlists() -> list[dict]:
    """Load all configured playlists, using 1-hour cache. Returns flat list of items."""
    raw = settings.YOUTUBE_PLAYLIST_URLS.strip()
    if not raw:
        return []

    urls = [u.strip() for u in raw.split(",") if u.strip()]
    now = time.time()

    # Fetch stale/missing playlists in parallel
    stale = [u for u in urls if u not in _playlist_cache or _playlist_cache[u]["expires"] < now]
    if stale:
        results = await asyncio.gather(*[_fetch_playlist(u) for u in stale])
        for url, items in zip(stale, results):
            _playlist_cache[url] = {"items": items, "expires": now + _PLAYLIST_TTL}

    all_items: list[dict] = []
    for u in urls:
        all_items.extend(_playlist_cache.get(u, {}).get("items", []))
    return all_items


def _search_playlists(items: list[dict], query: str) -> list[dict]:
    """Fuzzy-match query words against playlist item titles (case-insensitive substring)."""
    words = [w.lower() for w in query.split() if len(w) > 2]
    if not words:
        return []
    matched = []
    seen: set[str] = set()
    for item in items:
        title_lower = item["title"].lower()
        if any(w in title_lower for w in words) and item["video_id"] not in seen:
            seen.add(item["video_id"])
            matched.append({**item, "in_playlist": True})
    return matched


# ---------------------------------------------------------------------------
# YouTube search helper
# ---------------------------------------------------------------------------

def _fmt_duration(seconds) -> str:
    if not seconds:
        return "?"
    try:
        s = int(seconds)
        return f"{s // 60}:{s % 60:02d}"
    except Exception:
        return "?"


async def _search_youtube(query: str, count: int = 25) -> list[dict]:
    """Search YouTube for `count` results. Runs in executor."""
    import yt_dlp

    def _extract() -> list[dict]:
        opts = _ydl_opts_base()
        opts["extract_flat"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{count}:{query}", download=False)
            entries = info.get("entries") or []
            results = []
            for e in entries:
                vid = e.get("id") or ""
                if not vid:
                    continue
                results.append({
                    "video_id":    vid,
                    "title":       e.get("title") or "",
                    "uploader":    e.get("uploader") or e.get("channel") or "",
                    "duration_str": _fmt_duration(e.get("duration")),
                    "url":         f"https://www.youtube.com/watch?v={vid}",
                    "in_playlist": False,
                    "playlist_name": None,
                })
            return results

    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _extract),
            timeout=45,
        )
    except Exception as e:
        logger.warning("YouTube search failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# Download background task
# ---------------------------------------------------------------------------

async def _yt_download_task(download_id: str, url: str, title: str, language: str) -> None:
    dest = _DEST.get(language, _DEST["english"])
    state = _yt_downloads[download_id]
    state["status"] = "downloading"

    cookies = settings.YOUTUBE_COOKIES_FILE
    valid, reason = _validate_cookies_file(cookies)
    if not valid:
        state["status"] = "failed"
        state["error"] = (
            "YouTube cookies not configured. "
            "Export youtube_cookies.txt from Chrome (YouTube Premium) and upload to the VPS. "
            "See setup instructions."
        )
        logger.error("yt-dlp blocked: cookies file invalid at %s (%s)", cookies, reason)
        return

    cmd = [
        "yt-dlp",
        "--cookies", cookies,
        "--js-runtimes", "node",
        "--force-overwrites",
        "--format", "bestaudio[format_id=774]/bestaudio[acodec=opus]/bestaudio[ext=webm]/bestaudio",
        "--extract-audio", "--audio-format", "opus", "--audio-quality", "0",
        "--embed-thumbnail", "--embed-metadata",
        "--parse-metadata", "%(uploader)s:%(meta_artist)s",
        "--output", f"{dest}/%(uploader)s - %(title)s.%(ext)s",
        "--no-playlist",
        url,
    ]

    logger.info("yt-dlp download starting: %s → %s", title, dest)
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE)
        stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            state["status"] = "done"
            logger.info("yt-dlp done: %s", title)
            try:
                await navidrome.trigger_scan()
            except Exception as e:
                logger.warning("Navidrome scan failed after yt-dlp download: %s", e)
        else:
            err = stderr.decode(errors="replace")[-500:]
            state["status"] = "failed"
            state["error"] = err
            logger.error("yt-dlp failed (rc=%d): %s", proc.returncode, err)

    except Exception as e:
        state["status"] = "failed"
        state["error"] = str(e)
        logger.error("yt-dlp exception: %s", e)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str
    check_playlist: bool = True   # search configured playlists first


class DownloadRequest(BaseModel):
    search_id: str
    result_index: int             # 1-based, as shown to Sam
    language: str = "english"     # "english" | "hindi" | "punjabi"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/search")
async def youtube_search(req: SearchRequest, _: str = Depends(_require_api_key)):
    results: list[dict] = []
    seen_ids: set[str] = set()

    # 1. Playlist matches (if configured and requested)
    if req.check_playlist:
        playlist_items = await _load_playlists()
        if playlist_items:
            matches = _search_playlists(playlist_items, req.query)
            for item in matches:
                if item["video_id"] not in seen_ids:
                    seen_ids.add(item["video_id"])
                    item.setdefault("duration_str", "?")
                    results.append(item)

    # 2. General YouTube search
    yt_results = await _search_youtube(req.query, count=25)
    for item in yt_results:
        if item["video_id"] not in seen_ids and len(results) < 25:
            seen_ids.add(item["video_id"])
            results.append(item)

    # Assign 1-based index and strip internal video_id
    search_id = str(uuid.uuid4())
    indexed = []
    for i, r in enumerate(results, 1):
        entry = {
            "index":        i,
            "title":        r["title"],
            "uploader":     r["uploader"],
            "duration_str": r.get("duration_str", "?"),
            "url":          r["url"],
            "in_playlist":  r.get("in_playlist", False),
            "playlist_name": r.get("playlist_name"),
        }
        indexed.append(entry)

    # Cache full list (with URL) for download lookup
    _yt_search_cache[search_id] = {"results": [{"url": r["url"], **ir} for r, ir in zip(results, indexed)]}

    return {"search_id": search_id, "results": indexed}


@router.post("/download")
async def youtube_download(req: DownloadRequest, _: str = Depends(_require_api_key)):
    cached = _yt_search_cache.get(req.search_id)
    if not cached:
        raise HTTPException(status_code=404, detail="search_id not found or expired")

    results = cached["results"]
    if req.result_index < 1 or req.result_index > len(results):
        raise HTTPException(status_code=400, detail=f"result_index must be 1–{len(results)}")

    lang = req.language.lower()
    if lang not in _DEST:
        raise HTTPException(status_code=400, detail="language must be english | hindi | punjabi")

    entry = results[req.result_index - 1]
    url = entry["url"]
    title = entry["title"]

    download_id = str(uuid.uuid4())
    _yt_downloads[download_id] = {
        "status":   "starting",
        "title":    title,
        "language": lang,
        "error":    None,
    }

    asyncio.create_task(_yt_download_task(download_id, url, title, lang))

    return {
        "success":     True,
        "download_id": download_id,
        "title":       title,
        "language":    lang,
    }


@router.get("/status/{download_id}")
async def youtube_status(download_id: str, _: str = Depends(_require_api_key)):
    state = _yt_downloads.get(download_id)
    if not state:
        raise HTTPException(status_code=404, detail="download_id not found")
    return {"download_id": download_id, **state}
