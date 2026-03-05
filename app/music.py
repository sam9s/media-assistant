"""
Music pipeline router — /music/*

Endpoints:
  POST /music/search    — search Soulseek via slskd, return ranked FLAC results
  POST /music/download  — start slskd download for a chosen result
  GET  /music/status/{id} — poll download progress

Auth flow:
  slskd uses JWT (Bearer token). We login with SLSKD_USERNAME/PASSWORD and
  cache the token. It's valid for 7 days; we refresh with a 5-minute buffer.
"""

import asyncio
import logging
import os
import time
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from app.config import settings
from app.music_enrichment import enrich_and_deliver, enrich_single_track
from app.navidrome import search_album as navidrome_search

logger = logging.getLogger("uvicorn.error")

# Stuck-peer detection timeouts
_STUCK_NO_START_SECS = 600   # 10 min with 0 bytes → peer unresponsive → cancel
_STUCK_STALL_SECS    = 300   # 5 min since last byte → transfer stalled → cancel

router = APIRouter(prefix="/music", tags=["music"])

# ---------------------------------------------------------------------------
# Auth for our own API (X-API-Key header — same as main.py)
# ---------------------------------------------------------------------------
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: Optional[str] = Security(_api_key_header)) -> str:
    if not key or key != settings.API_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or missing API key")
    return key


# ---------------------------------------------------------------------------
# slskd JWT token cache
# ---------------------------------------------------------------------------
_jwt: dict = {"token": None, "expires": 0.0}


async def _slskd_token() -> str:
    """Return a valid slskd JWT token, refreshing if needed."""
    if _jwt["token"] and _jwt["expires"] > time.time() + 300:
        return _jwt["token"]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{settings.SLSKD_URL}/api/v0/session",
            json={"username": settings.SLSKD_USERNAME, "password": settings.SLSKD_PASSWORD},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=503, detail=f"slskd login failed: {r.status_code}")
    data = r.json()
    _jwt["token"] = data["token"]
    _jwt["expires"] = float(data["expires"])
    return _jwt["token"]


async def _slskd_headers() -> dict:
    token = await _slskd_token()
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# In-memory stores (reset on container restart — acceptable for our use case)
# ---------------------------------------------------------------------------
_search_cache: dict[str, dict] = {}   # search_id → {mode, results}
_downloads: dict[str, dict] = {}      # download_id → {language, peer, files, folder, status, ...}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class MusicSearchRequest(BaseModel):
    query: str
    artist: Optional[str] = None
    album: Optional[str] = None
    mode: str = "album"   # "album" | "track"


class MusicDownloadRequest(BaseModel):
    search_id: str
    result_index: int      # 1-based index from search results
    language: str          # "english" | "hindi" | "punjabi"


# ---------------------------------------------------------------------------
# Result parsing helpers
# ---------------------------------------------------------------------------

def _attr(attributes: list, type_id: int) -> Optional[int]:
    """Extract a Soulseek file attribute value by type."""
    for a in attributes:
        if a.get("type") == type_id:
            return a.get("value")
    return None


def _quality_label(file: dict) -> tuple[int, str]:
    """
    Returns (tier, label).  tier: 1=Hi-Res FLAC, 2=FLAC, 9=reject.
    Soulseek attribute types: 0=bitrate(kbps), 2=bitdepth, 4=samplerate.
    """
    filename = (file.get("filename") or "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("mp3", "m4a", "ogg", "aac", "wma"):
        return 9, "Lossy"
    if ext != "flac":
        return 9, ext.upper() or "Unknown"

    attrs = file.get("attributes") or []
    bit_depth   = _attr(attrs, 2)
    sample_rate = _attr(attrs, 4)

    if (bit_depth and bit_depth > 16) or (sample_rate and sample_rate > 48000):
        bd = f"{bit_depth}bit" if bit_depth else ""
        sr = f"{sample_rate // 1000}kHz" if sample_rate else ""
        label = f"Hi-Res FLAC ({' '.join(filter(None, [bd, sr]))})"
        return 1, label

    return 2, "FLAC"


def _remote_folder(filename: str) -> str:
    """Extract the remote directory from a Soulseek file path."""
    parts = filename.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else ""


def _parse_responses(responses: list) -> list[dict]:
    """
    Group files by (peer, folder), filter lossy, rank by quality then size.
    Returns up to 10 results.
    """
    folders: dict[tuple, dict] = {}

    for resp in responses:
        username = resp.get("username", "")
        for f in resp.get("files") or []:
            tier, label = _quality_label(f)
            if tier == 9:
                continue
            folder = _remote_folder(f.get("filename", ""))
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
            entry["files"].append({"filename": f.get("filename", ""), "size": size})
            entry["total_size"] += size
            if tier < entry["best_tier"]:
                entry["best_tier"] = tier
                entry["quality_label"] = label

    return sorted(folders.values(), key=lambda x: (x["best_tier"], -x["total_size"]))[:10]


def _parse_responses_tracks(responses: list) -> list[dict]:
    """
    Return individual FLAC files (track mode) instead of grouped album folders.
    Filters lossy formats. Sorts by quality tier then size desc. Returns up to 10.
    """
    tracks = []
    for resp in responses:
        username = resp.get("username", "")
        for f in resp.get("files") or []:
            tier, label = _quality_label(f)
            if tier == 9:
                continue
            filename = f.get("filename", "")
            size = f.get("size") or 0
            folder = _remote_folder(filename)
            basename = filename.replace("\\", "/").rsplit("/", 1)[-1] if "/" in filename.replace("\\", "/") else filename
            tracks.append({
                "peer_username": username,
                "filename": filename,
                "file_basename": basename,
                "folder_path": folder,
                "size_mb": round(size / 1_048_576, 1),
                "best_tier": tier,
                "quality_label": label,
            })
    return sorted(tracks, key=lambda x: (x["best_tier"], -x["size_mb"]))[:10]


# ---------------------------------------------------------------------------
# slskd search
# ---------------------------------------------------------------------------

async def _slskd_search(query: str, timeout_ms: int = 15000) -> list:
    """Run a slskd search and return ranked result list."""
    search_id = str(uuid.uuid4())
    hdrs = await _slskd_headers()

    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            f"{settings.SLSKD_URL}/api/v0/searches",
            headers=hdrs,
            json={
                "id": search_id,
                "searchText": query,
                "fileLimit": 200,
                "filterResponses": False,
                "timeout": timeout_ms,
            },
        )

        # Poll until Completed/Stopped (max 30s)
        for _ in range(30):
            await asyncio.sleep(1)
            r = await client.get(
                f"{settings.SLSKD_URL}/api/v0/searches/{search_id}",
                headers=hdrs,
            )
            if r.status_code == 200 and r.json().get("state", "").startswith("Completed"):
                break

        # Fetch responses
        r = await client.get(
            f"{settings.SLSKD_URL}/api/v0/searches/{search_id}/responses",
            headers=hdrs,
        )
        responses = r.json() if r.status_code == 200 else []

        # Clean up
        await client.delete(
            f"{settings.SLSKD_URL}/api/v0/searches/{search_id}",
            headers=hdrs,
        )

    return responses


# ---------------------------------------------------------------------------
# slskd download
# ---------------------------------------------------------------------------

async def _slskd_cancel_download(peer: str, transfer_id: str) -> None:
    """Cancel and remove a single slskd transfer."""
    try:
        hdrs = await _slskd_headers()
        async with httpx.AsyncClient(timeout=10) as client:
            await client.delete(
                f"{settings.SLSKD_URL}/api/v0/transfers/downloads/{peer}/{transfer_id}",
                headers=hdrs,
                params={"remove": "true"},
            )
        logger.info("Cancelled slskd transfer %s / %s", peer, transfer_id)
    except Exception as e:
        logger.warning("Failed to cancel slskd transfer %s: %s", transfer_id, e)


async def _slskd_download_files(peer_username: str, file_list: list[dict]) -> None:
    """Queue all files from a peer in a single batch POST."""
    hdrs = await _slskd_headers()
    payload = [{"filename": f["filename"], "size": f["size"]} for f in file_list]
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{settings.SLSKD_URL}/api/v0/transfers/downloads/{peer_username}",
            headers=hdrs,
            json=payload,
        )
    logger.info("slskd enqueue %s: HTTP %s, enqueued=%d failed=%d",
                peer_username, r.status_code,
                len((r.json() or {}).get("enqueued", [])),
                len((r.json() or {}).get("failed", [])))


async def _poll_and_enrich(download_id: str, peer_username: str, file_count: int) -> None:
    """Monitor slskd until all files complete, then run enrichment."""
    logger.info("Download poll: %s (%s, %d files)", download_id, peer_username, file_count)
    info = _downloads.get(download_id)
    if not info:
        return

    _downloads[download_id]["status"] = "downloading"

    our_files = {f["filename"] for f in info.get("files", [])}
    _start = time.monotonic()
    _last_bytes: int = 0
    _last_progress = _start
    _transfer_ids: dict[str, str] = {}   # filename → slskd transfer id
    completed = failed = 0               # track across loop (safe post-loop access)

    for _ in range(360):  # max 60 min
        await asyncio.sleep(10)
        try:
            hdrs = await _slskd_headers()
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{settings.SLSKD_URL}/api/v0/transfers/downloads/{peer_username}",
                    headers=hdrs,
                )
            data = r.json() if r.status_code == 200 else {}
            completed = failed = 0
            _cur_total_bytes: int = 0
            for directory in (data.get("directories") or []):
                for tf in directory.get("files") or []:
                    if tf.get("filename") in our_files:
                        tid = tf.get("id", "")
                        if tid:
                            _transfer_ids[tf["filename"]] = tid
                        tf_bytes = int(tf.get("bytesTransferred") or 0)
                        _cur_total_bytes += tf_bytes
                        st = (tf.get("state") or "").lower()
                        if "succeeded" in st and tf_bytes >= 65536:
                            completed += 1
                        elif "succeeded" in st or "completed" in st or "errored" in st or "cancelled" in st or "rejected" in st:
                            failed += 1
            logger.info("Download %s: %d/%d done, %d failed", download_id, completed, file_count, failed)
            if completed + failed >= file_count:
                break
            # Stuck-peer detection
            _now = time.monotonic()
            if _cur_total_bytes > _last_bytes:
                _last_bytes = _cur_total_bytes
                _last_progress = _now
            if not (completed or failed):
                no_start = (_last_bytes == 0) and ((_now - _start) >= _STUCK_NO_START_SECS)
                stalled  = (_last_bytes > 0)  and ((_now - _last_progress) >= _STUCK_STALL_SECS)
                if no_start or stalled:
                    reason = "unresponsive for 10 minutes" if no_start else "stalled for 5 minutes"
                    logger.warning("Album %s: peer %s %s — cancelling", download_id, peer_username, reason)
                    for tid in _transfer_ids.values():
                        await _slskd_cancel_download(peer_username, tid)
                    _downloads[download_id]["status"]  = "stuck"
                    _downloads[download_id]["message"] = (
                        f"Peer {peer_username} was {reason}. Download cancelled."
                    )
                    return
        except Exception as e:
            logger.warning("Transfer poll error: %s", e)

    if _downloads[download_id].get("status") != "downloading":
        return  # was marked stuck inside the loop
    if completed == 0:
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = (
            f"Peer {peer_username} rejected or failed the transfer."
        )
        return
    _downloads[download_id]["status"] = "enriching"

    # Derive local folder path: slskd saves to {downloads_dir}/{album_folder_name}/
    # (slskd uses only the last path component as the folder name, no peer subfolder)
    folder_path = info.get("folder_path", "")
    album_folder_name = folder_path.replace("\\", "/").rsplit("/", 1)[-1]
    download_dir = "/mnt/cloud/gdrive/Media/Music/Downloads"
    local_folder = f"{download_dir}/{album_folder_name}"

    await asyncio.sleep(3)  # let filesystem flush

    # Read actual bytes from each FLAC (bypasses GDrive VFS dentry cache)
    try:
        if not os.path.isdir(local_folder):
            raise OSError("folder does not exist")
        flac_files = [f for f in os.listdir(local_folder) if f.endswith(".flac")]
        if not flac_files:
            raise OSError("no FLAC files in folder")
        readable = []
        for fname in flac_files:
            fp = os.path.join(local_folder, fname)
            with open(fp, "rb") as fh:
                sample = fh.read(65536)
            if len(sample) >= 65536:
                readable.append(fname)
        if not readable:
            raise OSError("all FLAC files are too small or unreadable")
    except Exception as e:
        logger.error("Album enrichment: file check failed for %s — %s", local_folder, e)
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = (
            f"Peer {peer_username} signalled success but no valid files were written."
        )
        return

    success = await enrich_and_deliver(
        download_folder=local_folder,
        language=info["language"],
        artist_hint=info.get("artist", ""),
        album_hint=info.get("album", ""),
    )
    if not success:
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = "Enrichment failed — album could not be identified."
        return
    _downloads[download_id]["status"] = "done"


async def _poll_and_enrich_track(download_id: str, peer_username: str, filename: str) -> None:
    """Monitor slskd until a single file completes, then run single-track enrichment."""
    logger.info("Track poll: %s (%s, %s)", download_id, peer_username, filename)
    info = _downloads.get(download_id)
    if not info:
        return

    _downloads[download_id]["status"] = "downloading"

    _start = time.monotonic()
    _last_bytes: int = 0
    _last_progress = _start
    _transfer_id: Optional[str] = None
    _cur_bytes: int = 0
    completed = failed = 0               # track across loop (safe post-loop access)

    for _ in range(360):  # max 60 min
        await asyncio.sleep(10)
        try:
            hdrs = await _slskd_headers()
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{settings.SLSKD_URL}/api/v0/transfers/downloads/{peer_username}",
                    headers=hdrs,
                )
            data = r.json() if r.status_code == 200 else {}
            completed = failed = 0
            _cur_bytes = 0
            for directory in (data.get("directories") or []):
                for tf in directory.get("files") or []:
                    if tf.get("filename") == filename:
                        _transfer_id = tf.get("id") or _transfer_id
                        _cur_bytes = int(tf.get("bytesTransferred") or 0)
                        st = (tf.get("state") or "").lower()
                        if "succeeded" in st and _cur_bytes >= 65536:
                            completed += 1
                        elif "succeeded" in st or "completed" in st or "errored" in st or "cancelled" in st or "rejected" in st:
                            failed += 1
            logger.info("Track download %s: completed=%d failed=%d bytes=%d", download_id, completed, failed, _cur_bytes)
            if completed + failed >= 1:
                break
            # Stuck-peer detection
            _now = time.monotonic()
            if _cur_bytes > _last_bytes:
                _last_bytes = _cur_bytes
                _last_progress = _now
            if not (completed or failed):
                no_start = (_last_bytes == 0) and ((_now - _start) >= _STUCK_NO_START_SECS)
                stalled  = (_last_bytes > 0)  and ((_now - _last_progress) >= _STUCK_STALL_SECS)
                if no_start or stalled:
                    reason = "unresponsive for 10 minutes" if no_start else "stalled for 5 minutes"
                    logger.warning("Track %s: peer %s %s — cancelling", download_id, peer_username, reason)
                    if _transfer_id is not None:
                        await _slskd_cancel_download(peer_username, _transfer_id)
                    _downloads[download_id]["status"]  = "stuck"
                    _downloads[download_id]["message"] = (
                        f"Peer {peer_username} was {reason}. Download cancelled."
                    )
                    return
        except Exception as e:
            logger.warning("Track poll error: %s", e)

    if _downloads[download_id].get("status") != "downloading":
        return  # was marked stuck inside the loop
    if completed == 0:
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = (
            f"Peer {peer_username} rejected or failed the transfer."
        )
        return
    _downloads[download_id]["status"] = "enriching"

    # slskd saves single file to {downloads_dir}/{last_folder_component}/{basename}
    folder_name = _remote_folder(filename).replace("\\", "/").rsplit("/", 1)[-1]
    file_basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    download_dir = "/mnt/cloud/gdrive/Media/Music/Downloads"
    local_path = f"{download_dir}/{folder_name}/{file_basename}"

    await asyncio.sleep(3)  # let filesystem flush

    # Read actual bytes (bypasses GDrive VFS dentry cache which can lie about file existence)
    try:
        with open(local_path, "rb") as fh:
            sample = fh.read(65536)
        if len(sample) < 65536:
            raise OSError(f"only {len(sample)} bytes readable")
    except Exception as e:
        logger.error("Track enrichment: file check failed for %s — %s", local_path, e)
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = (
            f"Peer {peer_username} signalled success but no valid file was written."
        )
        return

    success = await enrich_single_track(
        flac_path=local_path,
        language=info["language"],
        title_hint=info.get("title", ""),
        artist_hint=info.get("artist", ""),
    )
    if not success:
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = "Enrichment failed — track could not be identified."
        return
    _downloads[download_id]["status"] = "done"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/search")
async def music_search(req: MusicSearchRequest, _: str = Depends(_require_api_key)):
    """Search Soulseek via slskd, return up to 10 ranked FLAC results.
    mode='album' (default) groups by folder; mode='track' returns individual files."""
    if not settings.SLSKD_PASSWORD:
        raise HTTPException(status_code=503, detail="SLSKD_PASSWORD not configured")

    already_in_navidrome = False
    if req.artist and req.album:
        already_in_navidrome = await navidrome_search(req.artist, req.album)

    raw_responses = await _slskd_search(req.query)
    mode = req.mode if req.mode in ("album", "track") else "album"

    if mode == "track":
        results_raw = _parse_responses_tracks(raw_responses)
    else:
        results_raw = _parse_responses(raw_responses)

    search_id = str(uuid.uuid4())
    _search_cache[search_id] = {"mode": mode, "results": results_raw}

    results = []
    if mode == "track":
        for i, r in enumerate(results_raw, 1):
            results.append({
                "index": i,
                "peer_username": r["peer_username"],
                "file_basename": r["file_basename"],
                "size_mb": r["size_mb"],
                "quality": r["quality_label"],
            })
    else:
        for i, r in enumerate(results_raw, 1):
            folder_name = r["folder_path"].replace("\\", "/").rsplit("/", 1)[-1]
            results.append({
                "index": i,
                "peer_username": r["peer_username"],
                "folder": folder_name,
                "file_count": len(r["files"]),
                "size_mb": round(r["total_size"] / 1_048_576, 1),
                "quality": r["quality_label"],
            })

    return {
        "search_id": search_id,
        "mode": mode,
        "already_in_navidrome": already_in_navidrome,
        "results": results,
    }


@router.post("/download")
async def music_download(req: MusicDownloadRequest, _: str = Depends(_require_api_key)):
    """Trigger slskd download for the chosen result. Returns immediately; enrichment runs in background."""
    cached = _search_cache.get(req.search_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Search ID not found or expired. Re-search first.")

    mode = cached.get("mode", "album")
    results_raw = cached.get("results", [])

    idx = req.result_index - 1
    if idx < 0 or idx >= len(results_raw):
        raise HTTPException(status_code=400, detail=f"result_index must be 1–{len(results_raw)}")

    result = results_raw[idx]
    peer = result["peer_username"]
    download_id = str(uuid.uuid4())

    if mode == "track":
        filename = result["filename"]
        file_size = int(result["size_mb"] * 1_048_576)
        file_list = [{"filename": filename, "size": file_size}]

        _downloads[download_id] = {
            "status": "starting",
            "language": req.language.lower(),
            "peer_username": peer,
            "files": file_list,
            "filename": filename,
            "title": result.get("file_basename", "").rsplit(".", 1)[0],
            "artist": "",
        }

        await _slskd_download_files(peer, file_list)
        _downloads[download_id]["status"] = "downloading"
        asyncio.create_task(_poll_and_enrich_track(download_id, peer, filename))

        return {
            "success": True,
            "download_id": download_id,
            "files": 1,
            "peer": peer,
            "track": result.get("file_basename", ""),
            "quality": result["quality_label"],
            "language": req.language,
            "destination": "Misc/",
        }

    else:
        files = result["files"]

        _downloads[download_id] = {
            "status": "starting",
            "language": req.language.lower(),
            "peer_username": peer,
            "files": files,  # list of {filename, size} dicts
            "folder_path": result["folder_path"],
            "artist": "",
            "album": "",
        }

        await _slskd_download_files(peer, files)
        _downloads[download_id]["status"] = "downloading"
        asyncio.create_task(_poll_and_enrich(download_id, peer, len(files)))

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
    """Poll download + enrichment status."""
    info = _downloads.get(download_id)
    if not info:
        raise HTTPException(status_code=404, detail="Download ID not found")
    resp: dict = {
        "download_id": download_id,
        "status": info.get("status"),
        "language": info.get("language"),
        "peer": info.get("peer_username"),
    }
    if info.get("message"):
        resp["message"] = info["message"]
    return resp
