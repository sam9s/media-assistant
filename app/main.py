import asyncio
import itertools
import logging
import os
import re as _re
import shutil
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

logger = logging.getLogger("uvicorn.error")

from app.config import settings
from app.iptorrents import search_iptorrents
from app.jackett import search_jackett
from app.jellyfin import JellyfinClient
from app.privatehd import search_privatehd
from app.qbittorrent import QBittorrentClient
from app.tmdb import TMDBClient

app = FastAPI(title="Sam's Media API", version="2.1.0")


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    body = await request.body()
    logger.error("422 on %s — body: %r — errors: %s", request.url.path, body, exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


# ---------------------------------------------------------------------------
# API Key security
# ---------------------------------------------------------------------------
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(key: Optional[str] = Security(api_key_header)) -> str:
    if not key or key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )
    return key


# ---------------------------------------------------------------------------
# Shared clients
# ---------------------------------------------------------------------------
qbt = QBittorrentClient(
    url=settings.QB_URL,
    username=settings.QB_USERNAME,
    password=settings.QB_PASSWORD,
)

jellyfin = JellyfinClient(
    url=settings.JELLYFIN_URL,
    api_key=settings.JELLYFIN_API_KEY,
)

tmdb = TMDBClient(api_key=settings.TMDB_API_KEY)

# ---------------------------------------------------------------------------
# Path mappings
# ---------------------------------------------------------------------------

# Where qBittorrent saves completed downloads (container-internal paths)
SAVE_PATHS: dict[str, str] = {
    "hollywood":      "/downloads/complete/Movies/Hollywood",
    "hindi":          "/downloads/complete/Movies/Hindi",
    "tv-hollywood":   "/downloads/complete/TV/Hollywood",
    "tv-indian":      "/downloads/complete/TV/Indian",
    "music-english":  "/downloads/complete/Music/English",
    "music-hindi":    "/downloads/complete/Music/Hindi",
    "music-punjabi":  "/downloads/complete/Music/Punjabi",
}

# Where to copy completed+renamed files.
# /mnt/cloud/gdrive/Media is a live rclone FUSE mount — writing here writes to Google Drive.
# Jellyfin also mounts this exact path as /media inside its container, so one copy
# achieves both: Jellyfin library AND permanent Google Drive archive.
MEDIA_PATHS: dict[str, str] = {
    "hollywood":      "/mnt/cloud/gdrive/Media/Movies/Hollywood",
    "hindi":          "/mnt/cloud/gdrive/Media/Movies/Hindi",
    "tv-hollywood":   "/mnt/cloud/gdrive/Media/TV/Hollywood",
    "tv-indian":      "/mnt/cloud/gdrive/Media/TV/Indian",
    "music-english":  "/mnt/cloud/gdrive/Media/Music/English",
    "music-hindi":    "/mnt/cloud/gdrive/Media/Music/Hindi",
    "music-punjabi":  "/mnt/cloud/gdrive/Media/Music/Punjabi",
}

# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


_VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".mov"}


def _safe_name(name: str) -> str:
    """Strip/replace characters that are invalid in Linux/macOS filenames."""
    name = _re.sub(r'[<>:"/\\?*\x00-\x1f]', "", name)  # strip truly invalid chars
    name = name.replace("|", " ").strip()               # pipe → space (our separator)
    return _re.sub(r" {2,}", " ", name)                 # collapse multiple spaces


def _largest_video(path: str) -> Optional[str]:
    """Return the path of the largest video file under *path* (file or dir)."""
    candidates: list[tuple[int, str]] = []
    if os.path.isfile(path):
        if os.path.splitext(path)[1].lower() in _VIDEO_EXTS:
            candidates.append((os.path.getsize(path), path))
    else:
        for root, _dirs, files in os.walk(path):
            for f in files:
                if os.path.splitext(f)[1].lower() in _VIDEO_EXTS:
                    fp = os.path.join(root, f)
                    candidates.append((os.path.getsize(fp), fp))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def _size_to_gb(size_str: str) -> Optional[float]:
    """Parse human-readable size string (e.g. '14.2 GB', '800 MB') to GB float."""
    m = _re.match(r"([\d.]+)\s*(TB|GB|MB|B)", size_str.strip(), _re.IGNORECASE)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).upper()
    if unit == "TB":
        return n * 1000
    if unit == "GB":
        return n
    if unit == "MB":
        return n / 1000
    return None


class SearchRequest(BaseModel):
    query: str
    quality: Optional[str] = None
    limit: int = 5
    min_size_gb: Optional[float] = None   # e.g. 10.0 — filter out results smaller than this
    max_size_gb: Optional[float] = None   # e.g. 15.0 — filter out results larger than this


class DownloadRequest(BaseModel):
    torrent_url: str
    category: str
    title: str
    year: Optional[int] = None   # TMDB year — stored as qBittorrent tag for post-download rename


class CompleteRequest(BaseModel):
    name: str          # %N — torrent name (qBittorrent variable)
    category: str      # %L — category label
    content_path: str  # %F — file path (single) or folder path (multi-file)
    info_hash: str     # %I — torrent info hash


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/search")
async def search(req: SearchRequest, _: str = Depends(require_api_key)):
    """
    Search Jackett (all configured trackers) + iptorrents in parallel, enrich with TMDB metadata.
    Returns merged results sorted by seeders, plus cover art and IMDb link.
    """
    # All lookups run concurrently — no waiting for one before the other
    jackett_task = asyncio.create_task(
        search_jackett(
            base_url=settings.JACKETT_URL,
            api_key=settings.JACKETT_API_KEY,
            query=req.query,
            quality=req.quality,
            limit=req.limit,
        )
    )
    ipt_task = asyncio.create_task(
        search_iptorrents(
            rss_base_url=settings.IPTORRENTS_RSS_BASE_URL,
            query=req.query,
            quality=req.quality,
            limit=req.limit,
        )
    )
    phd_task = asyncio.create_task(
        search_privatehd(
            jackett_url=settings.JACKETT_URL,
            jackett_api_key=settings.JACKETT_API_KEY,
            query=req.query,
            quality=req.quality,
            limit=req.limit,
        )
        if settings.JACKETT_API_KEY
        else asyncio.sleep(0, result=[])
    )
    tmdb_task = asyncio.create_task(tmdb.get_metadata(req.query))

    _j_raw, _ipt_raw, _phd_raw, _meta_raw = await asyncio.gather(
        jackett_task, ipt_task, phd_task, tmdb_task, return_exceptions=True
    )

    # Gracefully degrade — a single source failing won't kill the whole request
    jackett_results: list = _j_raw if isinstance(_j_raw, list) else []
    ipt_results: list = _ipt_raw if isinstance(_ipt_raw, list) else []
    phd_results: list = _phd_raw if isinstance(_phd_raw, list) else []
    metadata = _meta_raw if not isinstance(_meta_raw, Exception) else None

    # Apply optional size filter, then take up to limit from each source independently
    def _size_ok(r: dict) -> bool:
        if req.min_size_gb is None and req.max_size_gb is None:
            return True
        gb = _size_to_gb(r.get("size", ""))
        if gb is None:
            return True  # unknown size — include rather than discard
        min_gb = req.min_size_gb
        max_gb = req.max_size_gb
        if min_gb is not None and gb < min_gb:
            return False
        if max_gb is not None and gb > max_gb:
            return False
        return True

    jackett_top = list(itertools.islice(filter(_size_ok, jackett_results), req.limit))
    phd_top = list(itertools.islice(filter(_size_ok, phd_results), req.limit))
    ipt_top = list(itertools.islice(filter(_size_ok, ipt_results), req.limit))

    # PrivateHD first (best quality private tracker), then Jackett, then iptorrents
    top = phd_top + jackett_top + ipt_top
    for i, item in enumerate(top, start=1):
        item["index"] = i

    # Build per-tracker source counts dynamically from result "source" field
    sources: dict[str, int] = {}
    for r in top:
        src = r.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1

    return {
        "query": req.query,
        "metadata": metadata,   # poster_url, rating, imdb_url, overview, year
        "results": top,
        "total_found": len(top),
        "sources": sources,     # e.g. {"PrivateHD": 3, "1337x": 2, "iptorrents": 5}
    }


@app.post("/download")
async def download(req: DownloadRequest, _: str = Depends(require_api_key)):
    save_path = SAVE_PATHS.get(req.category)
    if save_path is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown category '{req.category}'. Valid: {list(SAVE_PATHS.keys())}",
        )

    tag = f"{req.title}|{req.year}" if req.year else req.title
    try:
        await qbt.add_torrent_from_url(
            torrent_url=req.torrent_url,
            save_path=save_path,
            category=req.category,
            tags=tag,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to add torrent to qBittorrent: [{type(exc).__name__}] {exc}",
        )

    return {
        "success": True,
        "message": f"{req.title} added to qBittorrent",
        "save_path": save_path,
    }


@app.get("/status")
async def get_status(title: Optional[str] = None, _: str = Depends(require_api_key)):
    try:
        active_downloads = await qbt.get_active_downloads()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to reach qBittorrent: {exc}",
        )

    jellyfin_match = None
    if title:
        try:
            jellyfin_match = await jellyfin.search(title)
        except Exception:
            jellyfin_match = {
                "found": False,
                "title": None,
                "year": None,
                "already_in_library": False,
            }

    response: dict = {"active_downloads": active_downloads}
    if jellyfin_match is not None:
        response["jellyfin_match"] = jellyfin_match
    return response


@app.post("/complete")
async def on_complete(req: CompleteRequest, _: str = Depends(require_api_key)):
    """
    Called by qBittorrent's 'Run External Program' when a torrent finishes.
    Renames the downloaded file(s) to 'Title (Year).ext' and triggers Jellyfin refresh.

    Configure in qBittorrent → Settings → Downloads → Run on torrent completion:
      curl -s -X POST http://172.17.0.1:8765/complete \\
        -H "X-API-Key: YOUR_KEY" \\
        -H "Content-Type: application/json" \\
        -d "{\"name\":\"%N\",\"category\":\"%L\",\"content_path\":\"%F\",\"info_hash\":\"%I\"}"
    Note: 172.17.0.1 is the Docker bridge gateway (host IP as seen from inside containers).
    """
    # 1. Retrieve clean title + year from the tag we stored at download time
    try:
        title, year = await qbt.get_torrent_tags(req.info_hash)
    except Exception:
        # Fallback: req.name may itself be "Title|Year" if qBittorrent lookup fails
        # (qBittorrent passes %N which is the torrent name, but we store "Title|Year" as tag)
        parts = req.name.split("|", 1)
        title = parts[0].strip()
        year = None
        if len(parts) > 1:
            try:
                year = int(parts[1].strip())
            except ValueError:
                pass

    clean = _safe_name(title)
    suffix = f" ({year})" if year else ""
    renamed: list[str] = []

    # 2. Rename the content — single file or top-level folder
    content = req.content_path.rstrip("/")
    if os.path.isfile(content):
        ext = os.path.splitext(content)[1]
        new_path = os.path.join(os.path.dirname(content), f"{clean}{suffix}{ext}")
        if content != new_path and not os.path.exists(new_path):
            os.rename(content, new_path)
            renamed.append(f"{clean}{suffix}{ext}")
            content = new_path

    elif os.path.isdir(content):
        # Rename the folder
        parent = os.path.dirname(content)
        new_dir = os.path.join(parent, f"{clean}{suffix}")
        if content != new_dir and not os.path.exists(new_dir):
            os.rename(content, new_dir)
            renamed.append(f"{clean}{suffix}/")
            content = new_dir

        # Rename the largest video file inside the folder
        main_video = _largest_video(content)
        if main_video:
            ext = os.path.splitext(main_video)[1]
            new_video = os.path.join(os.path.dirname(main_video), f"{clean}{suffix}{ext}")
            if main_video != new_video and not os.path.exists(new_video):
                os.rename(main_video, new_video)
                renamed.append(f"{clean}{suffix}{ext}")

    # 3. Copy renamed file to Google Drive FUSE mount (/mnt/cloud/gdrive/Media/...).
    #    This single copy serves both purposes:
    #    - Permanent Google Drive archive (via rclone FUSE mount)
    #    - Jellyfin library (Jellyfin mounts /mnt/cloud/gdrive/Media as /media internally)
    media_dest = MEDIA_PATHS.get(req.category)
    if media_dest and os.path.exists(content):
        os.makedirs(media_dest, exist_ok=True)
        dest = os.path.join(media_dest, os.path.basename(content))
        if not os.path.exists(dest):
            if os.path.isfile(content):
                shutil.copy2(content, dest)
            elif os.path.isdir(content):
                try:
                    shutil.copytree(content, dest, copy_function=shutil.copy)
                except shutil.Error:
                    # rclone FUSE mounts reject metadata ops (copystat) after data copy
                    # The file data was copied successfully; ignore metadata errors
                    pass

    # 4. Trigger Jellyfin library refresh so the new title appears immediately
    jf_refreshed = False
    try:
        await jellyfin.refresh_library()
        jf_refreshed = True
    except Exception:
        pass  # Jellyfin being unavailable should not fail the webhook

    return {"renamed": renamed, "jellyfin_refreshed": jf_refreshed}
