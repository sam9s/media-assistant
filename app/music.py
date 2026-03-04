"""
Music pipeline router — /music/*

Endpoints:
  POST /music/search    — search Soulseek via slskd, return ranked FLAC results
  POST /music/download  — start slskd download for a chosen result
  GET  /music/status/{id} — poll download progress
"""

import asyncio
import logging
import re
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from app.config import settings
from app.music_enrichment import enrich_and_deliver
from app.navidrome import search_album as navidrome_search

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/music", tags=["music"])

# ---------------------------------------------------------------------------
# Auth (same pattern as main.py)
# ---------------------------------------------------------------------------
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: Optional[str] = Security(_api_key_header)) -> str:
    if not key or key != settings.API_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or missing API key")
    return key


# ---------------------------------------------------------------------------
# In-memory stores  (reset on container restart — acceptable for our use case)
# ---------------------------------------------------------------------------
_search_cache: dict[str, list] = {}   # search_id → list of MusicResult dicts
_downloads: dict[str, dict] = {}      # download_id → {language, peer, files, folder, artist, album, status}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class MusicSearchRequest(BaseModel):
    query: str
    artist: Optional[str] = None
    album: Optional[str] = None


class MusicDownloadRequest(BaseModel):
    search_id: str
    result_index: int      # 1-based index from the search results
    language: str          # "english" | "hindi" | "punjabi"


# ---------------------------------------------------------------------------
# slskd helpers
# ---------------------------------------------------------------------------

def _slskd_headers() -> dict:
    return {"X-API-Key": settings.SLSKD_API_KEY}


def _attr(attributes: list, type_id: int) -> Optional[int]:
    """Extract an attribute value by type from a slskd file attributes list."""
    for a in attributes:
        if a.get("type") == type_id:
            return a.get("value")
    return None


def _quality_label(file: dict) -> tuple[int, str]:
    """
    Returns (tier, label) where tier: 1=Hi-Res, 2=FLAC, 9=reject.
    Attributes: type 0=bitrate(kbps), type 2=bitdepth, type 4=samplerate.
    """
    ext = (file.get("filename") or "").rsplit(".", 1)[-1].lower()
    if ext in ("mp3", "m4a", "ogg", "aac", "wma"):
        return 9, "MP3/Lossy"

    if ext != "flac":
        return 9, ext.upper()

    attrs = file.get("attributes") or []
    bit_depth  = _attr(attrs, 2)
    sample_rate = _attr(attrs, 4)

    if (bit_depth and bit_depth > 16) or (sample_rate and sample_rate > 48000):
        sr_label = f"{sample_rate // 1000}kHz" if sample_rate else ""
        bd_label = f"{bit_depth}bit" if bit_depth else ""
        label = f"Hi-Res FLAC ({bd_label} {sr_label})".strip("() ")
        return 1, label

    return 2, "FLAC"


def _remote_folder(filename: str) -> str:
    """Extract the remote folder from a Soulseek file path (backslash-separated)."""
    parts = filename.replace("\\", "/").split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else ""


def _parse_responses(responses: list) -> list[dict]:
    """
    Group files by peer+folder, filter to FLAC-only folders, rank by quality.
    Returns list of result dicts sorted by tier then size desc.
    """
    folders: dict[tuple, dict] = {}  # (username, folder) → result dict

    for resp in responses:
        username = resp.get("username", "")
        for f in resp.get("files") or []:
            filename = f.get("filename", "")
            ext = filename.rsplit(".", 1)[-1].lower()
            tier, label = _quality_label(f)
            if tier == 9:
                continue  # skip lossy

            folder = _remote_folder(filename)
            key = (username, folder)
            size = f.get("size") or 0

            if key not in folders:
                folders[key] = {
                    "peer_username": username,
                    "folder_path": folder,
                    "files": [],
                    "total_size": 0,
                    "best_tier": tier,
                    "quality_label": label,
                }

            entry = folders[key]
            entry["files"].append(filename)
            entry["total_size"] += size
            if tier < entry["best_tier"]:
                entry["best_tier"] = tier
                entry["quality_label"] = label

    results = sorted(folders.values(), key=lambda x: (x["best_tier"], -x["total_size"]))
    return results


async def _slskd_search(query: str, timeout_ms: int = 8000) -> list:
    """Run a slskd search and return parsed/ranked results (top 10 folders)."""
    search_id = str(uuid.uuid4())
    async with httpx.AsyncClient(timeout=15) as client:
        # Start search
        await client.post(
            f"{settings.SLSKD_URL}/api/v1/searches",
            headers=_slskd_headers(),
            json={
                "id": search_id,
                "searchText": query,
                "fileLimit": 200,
                "filterResponses": True,
                "minimumPeerUploadSpeed": 0,
                "timeout": timeout_ms,
            },
        )

        # Poll until completed or timeout
        for _ in range(30):
            await asyncio.sleep(1)
            r = await client.get(
                f"{settings.SLSKD_URL}/api/v1/searches/{search_id}",
                headers=_slskd_headers(),
            )
            data = r.json()
            if data.get("state") in ("Completed", "Stopped"):
                break

        # Fetch full results
        r = await client.get(
            f"{settings.SLSKD_URL}/api/v1/searches/{search_id}/responses",
            headers=_slskd_headers(),
        )
        responses = r.json() if r.status_code == 200 else []

        # Clean up search
        await client.delete(
            f"{settings.SLSKD_URL}/api/v1/searches/{search_id}",
            headers=_slskd_headers(),
        )

    return _parse_responses(responses)[:10]


async def _slskd_download_folder(peer_username: str, file_list: list[str]) -> None:
    """Initiate download for every file in the list from a Soulseek peer."""
    async with httpx.AsyncClient(timeout=30) as client:
        for filename in file_list:
            await client.post(
                f"{settings.SLSKD_URL}/api/v1/transfers/downloads/{peer_username}",
                headers=_slskd_headers(),
                json={"filename": filename},
            )


async def _poll_download_completion(download_id: str, peer_username: str, file_count: int) -> None:
    """
    Poll slskd transfers for the peer until all files are Completed or Failed.
    Then kick off enrichment as another background task.
    """
    logger.info("Download poll started: %s from %s (%d files)", download_id, peer_username, file_count)
    info = _downloads.get(download_id)
    if not info:
        return

    _downloads[download_id]["status"] = "downloading"

    for _ in range(360):  # max 60 minutes
        await asyncio.sleep(10)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{settings.SLSKD_URL}/api/v1/transfers/downloads/{peer_username}",
                    headers=_slskd_headers(),
                )
            transfers = r.json() if r.status_code == 200 else []
            # Each item is a directory group with a "files" list
            our_files = set(info.get("files", []))
            completed, failed, in_progress = 0, 0, 0
            for group in transfers:
                for tf in group.get("files") or []:
                    if tf.get("filename") in our_files:
                        st = (tf.get("state") or "").lower()
                        if "completed" in st:
                            completed += 1
                        elif "errored" in st or "cancelled" in st:
                            failed += 1
                        else:
                            in_progress += 1
            logger.info("Download %s: %d/%d completed, %d failed", download_id, completed, file_count, failed)
            if completed + failed >= file_count:
                break
        except Exception as e:
            logger.warning("Transfer poll error: %s", e)

    _downloads[download_id]["status"] = "enriching"

    # Derive the local download folder path
    # slskd saves to: {downloads_dir}/{peer_username}/{subfolder_path}/
    # The folder_path stored is the remote folder (backslash-sep → we use the last component)
    remote_folder = info.get("folder_path", "")
    # Last segment after the last backslash or slash
    album_folder_name = remote_folder.replace("\\", "/").rsplit("/", 1)[-1]
    download_dir = info.get("download_dir", "/mnt/cloud/gdrive/Media/Music/Downloads")
    local_folder = f"{download_dir}/{peer_username}/{album_folder_name}"

    # Wait a moment for filesystem flush
    await asyncio.sleep(3)

    await enrich_and_deliver(
        download_folder=local_folder,
        language=info["language"],
        artist_hint=info.get("artist", ""),
        album_hint=info.get("album", ""),
    )
    _downloads[download_id]["status"] = "done"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/search")
async def music_search(req: MusicSearchRequest, _: str = Depends(_require_api_key)):
    """
    Search Soulseek via slskd. Returns up to 10 ranked FLAC album results.
    """
    if not settings.SLSKD_API_KEY:
        raise HTTPException(status_code=503, detail="SLSKD_API_KEY not configured")

    # Navidrome existence check
    already_in_navidrome = False
    if req.artist and req.album:
        already_in_navidrome = await navidrome_search(req.artist, req.album)

    results_raw = await _slskd_search(req.query)

    search_id = str(uuid.uuid4())
    _search_cache[search_id] = results_raw

    # Format for response
    results = []
    for i, r in enumerate(results_raw, 1):
        results.append({
            "index": i,
            "peer_username": r["peer_username"],
            "folder": r["folder_path"].replace("\\", "/").rsplit("/", 1)[-1],
            "file_count": len(r["files"]),
            "size_mb": round(r["total_size"] / 1_048_576, 1),
            "quality": r["quality_label"],
        })

    return {
        "search_id": search_id,
        "already_in_navidrome": already_in_navidrome,
        "results": results,
    }


@router.post("/download")
async def music_download(req: MusicDownloadRequest, _: str = Depends(_require_api_key)):
    """
    Start downloading the selected result from slskd.
    Returns immediately; enrichment runs in the background.
    """
    cached = _search_cache.get(req.search_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Search ID not found or expired. Re-search first.")

    idx = req.result_index - 1
    if idx < 0 or idx >= len(cached):
        raise HTTPException(status_code=400, detail=f"result_index must be between 1 and {len(cached)}")

    result = cached[idx]
    peer = result["peer_username"]
    files = result["files"]
    folder_path = result["folder_path"]

    download_id = str(uuid.uuid4())
    _downloads[download_id] = {
        "status": "starting",
        "language": req.language.lower(),
        "peer_username": peer,
        "files": set(files),
        "folder_path": folder_path,
        "artist": "",   # extracted from folder name heuristic below
        "album": "",
        "download_dir": "/mnt/cloud/gdrive/Media/Music/Downloads",
    }

    await _slskd_download_folder(peer, files)
    _downloads[download_id]["status"] = "downloading"

    # Start background completion monitor
    asyncio.create_task(
        _poll_download_completion(download_id, peer, len(files))
    )

    return {
        "success": True,
        "download_id": download_id,
        "files": len(files),
        "peer": peer,
        "quality": result["quality_label"],
        "language": req.language,
    }


@router.get("/status/{download_id}")
async def music_status(download_id: str, _: str = Depends(_require_api_key)):
    """Check the status of a download + enrichment job."""
    info = _downloads.get(download_id)
    if not info:
        raise HTTPException(status_code=404, detail="Download ID not found")
    return {
        "download_id": download_id,
        "status": info.get("status", "unknown"),
        "language": info.get("language"),
        "peer": info.get("peer_username"),
    }
