import asyncio
import io
import itertools
import json
import logging
import os
import re as _re
import shutil
import zipfile
from pathlib import Path
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
from app.librarian import router as librarian_router
from app.music import router as music_router
from app.opensubtitles import OpenSubtitlesClient
from app.privatehd import search_privatehd
from app.qbittorrent import QBittorrentClient
from app.subdl import SubDLClient
from app.tmdb import TMDBClient

app = FastAPI(title="Sam's Media API", version="3.0.0")
app.include_router(librarian_router)
app.include_router(music_router)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    body = await request.body()
    logger.error("422 on %s â€” body: %r â€” errors: %s", request.url.path, body, exc.errors())
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

opensubtitles = OpenSubtitlesClient(
    api_key=settings.OPENSUBTITLES_API_KEY,
    username=settings.OPENSUBTITLES_USERNAME,
    password=settings.OPENSUBTITLES_PASSWORD,
    languages=settings.OPENSUBTITLES_LANGUAGES,
    prefer_sdh=settings.OPENSUBTITLES_PREFER_SDH,
)

subdl = SubDLClient(
    api_key=settings.SUBDL_API_KEY,
    languages=settings.SUBDL_LANGUAGES,
)

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
# /mnt/cloud/gdrive/Media is a live rclone FUSE mount â€” writing here writes to Google Drive.
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
    name = name.replace("|", " ").strip()               # pipe â†’ space (our separator)
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


def _subtitle_path_for_video(video_path: str, language: str) -> str:
    stem = str(Path(video_path).with_suffix(""))
    return f"{stem}.{language}.srt"


def _subtitle_note_path(video_path: str) -> Path:
    return Path(video_path).with_suffix(".subtitle.json")


def _subtitle_sidecars_for_video(video_path: str) -> list[Path]:
    video = Path(video_path)
    base = video.with_suffix("")
    parent = base.parent
    stem = base.name
    exts = {".srt", ".ass", ".ssa", ".sub"}
    matches: list[Path] = []
    for candidate in parent.iterdir():
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in exts:
            continue
        if not candidate.name.startswith(f"{stem}."):
            continue
        matches.append(candidate)
    return matches


def _clear_subtitle_sidecars(video_path: str, keep_path: Optional[str] = None) -> list[str]:
    removed: list[str] = []
    keep = Path(keep_path) if keep_path else None
    for candidate in _subtitle_sidecars_for_video(video_path):
        if keep and candidate == keep:
            continue
        candidate.unlink(missing_ok=True)
        removed.append(str(candidate))
    return removed


def _normalize_release_name(name: str) -> str:
    text = (name or "").strip().lower()
    text = text.replace(".", " ").replace("_", " ")
    text = _re.sub(r"\.(srt|ass|ssa|sub)$", "", text)
    text = _re.sub(r"[^a-z0-9]+", " ", text)
    return _re.sub(r"\s+", " ", text).strip()


def _core_release_name(name: str) -> str:
    text = (name or "").strip()
    # Drop the trailing release group suffix, e.g. "-MoS", for fallback matching.
    text = _re.sub(r"[- ]+[A-Za-z0-9]{2,12}$", "", text)
    return _normalize_release_name(text)


def _token_set(name: str) -> set[str]:
    return set(_normalize_release_name(name).split())


def _subtitle_match_details(original_name: str, candidate_release: str) -> dict:
    original_norm = _normalize_release_name(original_name)
    candidate_norm = _normalize_release_name(candidate_release)
    original_core = _core_release_name(original_name)
    candidate_tokens = _token_set(candidate_release)
    core_tokens = set(original_core.split()) if original_core else set()
    overlap = len(core_tokens & candidate_tokens)

    exact = bool(original_norm and candidate_norm and original_norm == candidate_norm)
    core_match = bool(core_tokens) and core_tokens.issubset(candidate_tokens)

    score = overlap
    if core_match:
        score += 100
    if exact:
        score += 1000

    return {
        "exact": exact,
        "core_match": core_match,
        "score": score,
        "overlap": overlap,
    }


def _original_release_name(content_path: str, fallback_name: str) -> str:
    content = content_path.rstrip("/")
    if os.path.isfile(content):
        return Path(content).stem
    if os.path.isdir(content):
        largest = _largest_video(content)
        if largest:
            return Path(largest).stem
    return fallback_name


def _extract_subtitle_bytes(archive_bytes: bytes) -> tuple[bytes, str]:
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise RuntimeError("Subtitle archive is empty")

        def _rank(name: str) -> tuple[int, int]:
            ext = Path(name).suffix.lower()
            priority = {".srt": 0, ".ass": 1, ".ssa": 2, ".sub": 3}.get(ext, 99)
            return (priority, -len(name))

        chosen = sorted(names, key=_rank)[0]
        return zf.read(chosen), chosen


async def _download_best_subtitle(
    *,
    media_file: str,
    title: str,
    year: Optional[int],
    original_name: str,
    attempt: int = 0,
    replace_existing: bool = False,
) -> dict:
    if not settings.OPENSUBTITLES_AUTO_DOWNLOAD and not replace_existing:
        return {"status": "disabled"}

    language = (settings.OPENSUBTITLES_LANGUAGES or "en").split(",", 1)[0].strip() or "en"
    subtitle_path = _subtitle_path_for_video(media_file, language)

    if os.path.exists(subtitle_path) and not replace_existing:
        return {"status": "skipped", "reason": "subtitle already exists", "path": subtitle_path}

    candidates = await opensubtitles.search_candidates(
        title=title,
        year=year,
        original_name=original_name,
        languages=settings.OPENSUBTITLES_LANGUAGES,
    )
    if not candidates:
        return {"status": "not_found", "reason": "no subtitle candidates returned"}
    if attempt >= len(candidates):
        return {"status": "not_found", "reason": f"only {len(candidates)} candidates available"}

    selected = candidates[attempt]
    content, source_name = await opensubtitles.download_subtitle(selected["file_id"])
    os.makedirs(os.path.dirname(subtitle_path), exist_ok=True)
    with open(subtitle_path, "wb") as f:
        f.write(content)

    return {
        "status": "downloaded",
        "path": subtitle_path,
        "language": language,
        "attempt": attempt,
        "source_release": selected.get("release") or selected.get("file_name") or source_name,
        "candidate_count": len(candidates),
    }


class SearchRequest(BaseModel):
    query: str
    quality: Optional[str] = None
    limit: int = 5
    min_size_gb: Optional[float] = None   # e.g. 10.0 â€” filter out results smaller than this
    max_size_gb: Optional[float] = None   # e.g. 15.0 â€” filter out results larger than this


class DownloadRequest(BaseModel):
    torrent_url: str
    category: str
    title: str
    year: Optional[int] = None   # TMDB year â€” stored as qBittorrent tag for post-download rename


class CompleteRequest(BaseModel):
    name: str          # %N - torrent name (qBittorrent variable)
    category: str      # %L - category label
    content_path: str  # %F - file path (single) or folder path (multi-file)
    info_hash: str     # %I - torrent info hash


class SubtitleRetryRequest(BaseModel):
    media_path: str
    title: str
    year: Optional[int] = None
    original_name: Optional[str] = None
    attempt: int = 1
    replace_existing: bool = True


class SubtitleSearchRequest(BaseModel):
    title: str
    year: Optional[int] = None
    original_name: Optional[str] = None
    media_type: str = "movie"
    limit: int = 10


class SubtitleDownloadRequest(BaseModel):
    media_path: str
    download_url: str
    language: str = "en"
    release_name: Optional[str] = None
    replace_existing: bool = True


class SubtitleTryFallbackRequest(BaseModel):
    media_path: str
    title: str
    year: Optional[int] = None
    original_name: str
    choice: int = 1
    media_type: str = "movie"
    language: str = "en"
    limit: int = 10
    replace_existing: bool = True


class SubtitleClearRequest(BaseModel):
    media_path: str


class SubtitleOffsetRequest(BaseModel):
    media_path: str
    offset_seconds: float
    subtitle_file: Optional[str] = None
    note: Optional[str] = None


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
    # All lookups run concurrently â€” no waiting for one before the other
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

    # Gracefully degrade â€” a single source failing won't kill the whole request
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
            return True  # unknown size â€” include rather than discard
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


@app.post("/subtitles/search")
async def search_subtitles(req: SubtitleSearchRequest, _: str = Depends(require_api_key)):
    try:
        result = await asyncio.to_thread(
            subdl.search,
            title=req.title,
            year=req.year,
            original_name=req.original_name or "",
            media_type=req.media_type,
            limit=req.limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Subtitle search failed: [{type(exc).__name__}] {exc}",
        )

    subtitles = result.get("subtitles", [])
    exact_match = None
    exact_match_found = False
    fallback_candidates = []
    if req.original_name:
        enriched = []
        for item in subtitles:
            details = _subtitle_match_details(req.original_name, item.get("release_name") or "")
            enriched_item = {**item, "match": details}
            enriched.append(enriched_item)

        enriched.sort(key=lambda item: item["match"]["score"], reverse=True)
        subtitles = enriched
        exact_match = next((item for item in subtitles if item["match"]["exact"]), None)
        exact_match_found = exact_match is not None
        fallback_candidates = [item for item in subtitles if not item["match"]["exact"]][:3]

    return {
        "title": req.title,
        "year": req.year,
        "original_name": req.original_name,
        "source": "SubDL",
        "results": subtitles,
        "total_found": len(subtitles),
        "exact_match_found": exact_match_found,
        "exact_match": exact_match,
        "auto_download_allowed": exact_match_found,
        "manual_decision_required": bool(req.original_name and not exact_match_found and subtitles),
        "fallback_candidates": fallback_candidates,
    }


@app.post("/subtitles/download")
async def download_subtitle(req: SubtitleDownloadRequest, _: str = Depends(require_api_key)):
    if not os.path.isfile(req.media_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media file not found: {req.media_path}",
        )

    language = (req.language or "en").strip().lower() or "en"
    subtitle_path = _subtitle_path_for_video(req.media_path, language)
    if os.path.exists(subtitle_path) and not req.replace_existing:
        return {
            "status": "skipped",
            "reason": "subtitle already exists",
            "path": subtitle_path,
        }

    try:
        archive_bytes = await asyncio.to_thread(subdl.download_archive, req.download_url)
        subtitle_bytes, source_name = await asyncio.to_thread(_extract_subtitle_bytes, archive_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Subtitle download failed: [{type(exc).__name__}] {exc}",
        )

    try:
        removed = _clear_subtitle_sidecars(req.media_path) if req.replace_existing else []
        with open(subtitle_path, "wb") as f:
            f.write(subtitle_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save subtitle file: {exc}",
        )

    return {
        "status": "downloaded",
        "path": subtitle_path,
        "source_file": source_name,
        "release_name": req.release_name,
        "removed": removed,
    }


@app.post("/subtitles/try-fallback")
async def try_fallback_subtitle(req: SubtitleTryFallbackRequest, _: str = Depends(require_api_key)):
    if req.choice < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="choice must be 1 or greater",
        )
    if not os.path.isfile(req.media_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media file not found: {req.media_path}",
        )

    try:
        result = await asyncio.to_thread(
            subdl.search,
            title=req.title,
            year=req.year,
            original_name=req.original_name,
            media_type=req.media_type,
            limit=req.limit,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Subtitle search failed: [{type(exc).__name__}] {exc}",
        )

    subtitles = result.get("subtitles", [])
    ranked = []
    for item in subtitles:
        details = _subtitle_match_details(req.original_name, item.get("release_name") or "")
        ranked.append({**item, "match": details})
    ranked.sort(key=lambda item: item["match"]["score"], reverse=True)

    exact_match = next((item for item in ranked if item["match"]["exact"]), None)
    if exact_match is not None:
        return {
            "status": "exact_match_exists",
            "message": "An exact subtitle match exists; use the exact match instead of fallback.",
            "exact_match": exact_match,
        }

    fallback_candidates = [item for item in ranked if not item["match"]["exact"]]
    if not fallback_candidates:
        return {
            "status": "not_found",
            "message": "No subtitle candidates available.",
            "fallback_candidates": [],
        }

    index = req.choice - 1
    if index >= len(fallback_candidates):
        return {
            "status": "not_found",
            "message": f"Only {len(fallback_candidates)} fallback candidate(s) available.",
            "fallback_candidates": fallback_candidates[:3],
        }

    selected = fallback_candidates[index]
    language = (req.language or "en").strip().lower() or "en"
    subtitle_path = _subtitle_path_for_video(req.media_path, language)
    if os.path.exists(subtitle_path) and not req.replace_existing:
        return {
            "status": "skipped",
            "reason": "subtitle already exists",
            "path": subtitle_path,
        }

    try:
        archive_bytes = await asyncio.to_thread(subdl.download_archive, selected.get("download_url"))
        subtitle_bytes, source_name = await asyncio.to_thread(_extract_subtitle_bytes, archive_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Subtitle download failed: [{type(exc).__name__}] {exc}",
        )

    try:
        removed = _clear_subtitle_sidecars(req.media_path) if req.replace_existing else []
        with open(subtitle_path, "wb") as f:
            f.write(subtitle_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save subtitle file: {exc}",
        )

    return {
        "status": "downloaded",
        "path": subtitle_path,
        "source_file": source_name,
        "release_name": selected.get("release_name"),
        "choice": req.choice,
        "match": selected.get("match"),
        "removed": removed,
    }


@app.post("/subtitles/clear")
async def clear_subtitles(req: SubtitleClearRequest, _: str = Depends(require_api_key)):
    if not os.path.isfile(req.media_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media file not found: {req.media_path}",
        )

    try:
        removed = _clear_subtitle_sidecars(req.media_path)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear subtitle files: {exc}",
        )

    return {
        "status": "cleared",
        "media_path": req.media_path,
        "removed": removed,
        "removed_count": len(removed),
    }


@app.post("/subtitles/offset")
async def save_subtitle_offset(req: SubtitleOffsetRequest, _: str = Depends(require_api_key)):
    if not os.path.isfile(req.media_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media file not found: {req.media_path}",
        )

    note_path = _subtitle_note_path(req.media_path)
    payload = {
        "media_path": req.media_path,
        "subtitle_file": req.subtitle_file or Path(_subtitle_path_for_video(req.media_path, "en")).name,
        "offset_seconds": req.offset_seconds,
        "note": req.note or "",
    }

    try:
        with open(note_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save subtitle offset note: {exc}",
        )

    return {
        "status": "saved",
        "path": str(note_path),
        "offset_seconds": req.offset_seconds,
        "subtitle_file": payload["subtitle_file"],
    }


@app.get("/subtitles/offset")
async def get_subtitle_offset(media_path: str, _: str = Depends(require_api_key)):
    if not os.path.isfile(media_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Media file not found: {media_path}",
        )

    note_path = _subtitle_note_path(media_path)
    if not note_path.exists():
        return {
            "status": "not_found",
            "path": str(note_path),
            "offset": None,
        }

    try:
        with open(note_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read subtitle offset note: {exc}",
        )

    return {
        "status": "ok",
        "path": str(note_path),
        "offset": payload,
    }


@app.post("/complete")
async def on_complete(request: Request, _: str = Depends(require_api_key)):
    """
    Called by qBittorrent's 'Run External Program' when a torrent finishes.
    Renames the downloaded file(s) to 'Title (Year).ext', optionally downloads
    a subtitle, and triggers Jellyfin refresh.
    """
    body = await request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        data = json.loads(body.replace(b'\\"', b'"'))

    try:
        req = CompleteRequest(**data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        title, year = await qbt.get_torrent_tags(req.info_hash)
    except Exception:
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
    subtitle_result: Optional[dict] = None
    final_media_file: Optional[str] = None
    original_release_name = _original_release_name(req.content_path, req.name)

    content = req.content_path.rstrip("/")
    media_dest = MEDIA_PATHS.get(req.category)
    if media_dest and os.path.exists(content):
        os.makedirs(media_dest, exist_ok=True)
        if os.path.isfile(content):
            ext = os.path.splitext(content)[1]
            dest_name = f"{clean}{suffix}{ext}" if ext else f"{clean}{suffix}"
            dest = os.path.join(media_dest, dest_name)
            if not os.path.exists(dest):
                shutil.copy2(content, dest)
                renamed.append(dest_name)
            final_media_file = dest

        elif os.path.isdir(content):
            dest_name = f"{clean}{suffix}"
            dest = os.path.join(media_dest, dest_name)
            if not os.path.exists(dest):
                try:
                    shutil.copytree(content, dest, copy_function=shutil.copy)
                except shutil.Error:
                    pass
                renamed.append(f"{dest_name}/")

            if os.path.isdir(dest):
                main_video = _largest_video(dest)
                if main_video:
                    ext = os.path.splitext(main_video)[1]
                    new_video = os.path.join(os.path.dirname(main_video), f"{clean}{suffix}{ext}")
                    if main_video != new_video and not os.path.exists(new_video):
                        os.rename(main_video, new_video)
                        renamed.append(f"{clean}{suffix}{ext}")
                        final_media_file = new_video
                    else:
                        final_media_file = main_video

    jf_refreshed = False
    try:
        await jellyfin.refresh_library()
        jf_refreshed = True
    except Exception:
        pass

    return {
        "renamed": renamed,
        "subtitle": None,
        "jellyfin_refreshed": jf_refreshed,
        "original_release_name": original_release_name,
    }

