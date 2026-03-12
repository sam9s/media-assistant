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
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from app.config import settings
from app.job_store import get_job as db_get_job
from app.job_store import upsert_job
from app.music_enrichment import enrich_and_deliver, enrich_single_track
from app.notifications import send_telegram_message
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
    # Cap at 5 min to avoid stale tokens when slskd is restarted
    _jwt["expires"] = min(float(data["expires"]), time.time() + 300)
    return _jwt["token"]


async def _slskd_headers() -> dict:
    token = await _slskd_token()
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# In-memory stores (reset on container restart — acceptable for our use case)
# ---------------------------------------------------------------------------
_search_cache: dict[str, dict] = {}   # search_id → {mode, results}
_downloads: dict[str, dict] = {}      # download_id → {language, peer, files, folder, status, ...}


def _music_title(info: dict) -> str:
    return info.get("album") or info.get("title") or info.get("folder_path") or info.get("source_path") or "Music job"


async def _send_music_notification_once(download_id: str, kind: str, text: str) -> bool:
    info = _downloads.get(download_id)
    if not info:
        return False
    flag = f"{kind}_notification_sent"
    if info.get(flag):
        return False
    sent = await send_telegram_message(text)
    if sent:
        info[flag] = True
        info["updated_at"] = time.time()
        _persist_music_job(download_id)
    return sent


async def _music_start_check(download_id: str, delay_seconds: int = 25) -> None:
    await asyncio.sleep(delay_seconds)
    info = _downloads.get(download_id)
    if (
        not info
        or info.get("start_notification_sent")
        or info.get("progress_notification_sent")
        or info.get("terminal_notification_sent")
    ):
        return

    status = info.get("status")
    title = _music_title(info)
    if status in {"done", "failed", "stuck"}:
        return

    bytes_done = info.get("bytes_done") or 0
    files_done = info.get("files_done") or 0
    bytes_total = info.get("bytes_total")
    files_total = info.get("files_total")
    speed = info.get("speed_bytes_per_second")
    eta = info.get("eta_seconds")

    if bytes_done > 0 or files_done > 0:
        progress = info.get("progress_percent")
        parts = [f"{title} download started successfully."]
        if progress is not None:
            parts.append(f"Progress: {progress:.1f}%.")
        if bytes_total:
            parts.append(f"Transferred: {round(bytes_done / 1_048_576, 1)} MB / {round(bytes_total / 1_048_576, 1)} MB.")
        if files_total:
            parts.append(f"Files: {files_done}/{files_total}.")
        if speed:
            parts.append(f"Speed: {round(speed / 1_048_576, 2)} MB/s.")
        if eta:
            parts.append(f"ETA: {eta}s.")
        await _send_music_notification_once(download_id, "start", " ".join(parts))
        return

    if status in {"starting", "downloading"}:
        await _send_music_notification_once(
            download_id,
            "start",
            f"{title} is queued but no transfer progress is visible yet. I'm still tracking it.",
        )


async def _maybe_send_music_progress_confirm(download_id: str) -> None:
    info = _downloads.get(download_id)
    if not info or info.get("progress_notification_sent") or info.get("terminal_notification_sent"):
        return
    bytes_done = info.get("bytes_done") or 0
    progress = info.get("progress_percent")
    speed = info.get("speed_bytes_per_second") or 0
    meaningful_transfer = _has_meaningful_music_transfer(info)
    if not meaningful_transfer or speed <= 0:
        return

    title = _music_title(info)
    bytes_total = info.get("bytes_total")
    eta = info.get("eta_seconds")
    files_total = info.get("files_total")

    parts = [f"{title} is now transferring normally."]
    if progress is not None:
        parts.append(f"Progress: {progress:.1f}%.")
    if bytes_total:
        parts.append(f"Transferred: {round(bytes_done / 1_048_576, 1)} MB / {round(bytes_total / 1_048_576, 1)} MB.")
    if files_total:
        parts.append(f"Files: {files_done}/{files_total}.")
    if speed:
        parts.append(f"Speed: {round(speed / 1_048_576, 2)} MB/s.")
    if eta:
        parts.append(f"ETA: {eta}s.")
    parts.append("This one looks healthy so far.")
    await _send_music_notification_once(download_id, "progress", " ".join(parts))


def _persist_music_job(download_id: str) -> None:
    info = _downloads.get(download_id)
    if not info:
        return
    upsert_job(
        job_id=download_id,
        pipeline="music",
        title=_music_title(info),
        status=info.get("status") or "unknown",
        progress_percent=info.get("progress_percent"),
        bytes_done=info.get("bytes_done"),
        bytes_total=info.get("bytes_total"),
        speed_bytes_per_second=info.get("speed_bytes_per_second"),
        eta_seconds=info.get("eta_seconds"),
        files_done=info.get("files_done"),
        files_total=info.get("files_total"),
        message=info.get("message"),
        started_at=info.get("started_at"),
        payload={
            "language": info.get("language"),
            "peer": info.get("peer_username"),
            "mode": info.get("mode"),
            "active_result_index": info.get("active_result_index"),
            "retry_chain": [c.get("result_index") for c in (info.get("candidate_queue") or [])],
            "attempt_history": info.get("attempt_history") or [],
        },
    )


def _has_meaningful_music_transfer(info: dict) -> bool:
    bytes_done = info.get("bytes_done") or 0
    files_done = info.get("files_done") or 0
    progress = info.get("progress_percent")
    return (
        bytes_done >= 5 * 1024 * 1024
        or files_done >= 1
        or (progress is not None and progress >= 1.0)
    )


async def _activate_music_candidate(download_id: str, candidate: dict) -> None:
    info = _downloads[download_id]
    mode = candidate["mode"]
    info["peer_username"] = candidate["peer_username"]
    info["mode"] = mode
    info["attempt_number"] = candidate["attempt_number"]
    info["active_result_index"] = candidate["result_index"]
    info["status"] = "starting"
    info["bytes_done"] = 0
    info["progress_percent"] = 0.0
    info["speed_bytes_per_second"] = None
    info["eta_seconds"] = None
    info["files_done"] = 0
    info["updated_at"] = time.time()
    info["start_notification_sent"] = False
    info["progress_notification_sent"] = False

    if mode == "track":
        info["files"] = candidate["files"]
        info["filename"] = candidate["filename"]
        info["title"] = candidate.get("title", "")
        info["artist"] = candidate.get("artist", "")
        info["bytes_total"] = candidate["bytes_total"]
        info["files_total"] = 1
        _persist_music_job(download_id)
        await _slskd_download_files(candidate["peer_username"], candidate["files"])
        info["status"] = "downloading"
        info["updated_at"] = time.time()
        _persist_music_job(download_id)
        asyncio.create_task(_music_start_check(download_id, delay_seconds=20))
        asyncio.create_task(_poll_and_enrich_track(download_id, candidate["peer_username"], candidate["filename"]))
        return

    info["files"] = candidate["files"]
    info["folder_path"] = candidate["folder_path"]
    info["artist"] = candidate.get("artist", "")
    info["album"] = candidate.get("album", "")
    info["bytes_total"] = candidate["bytes_total"]
    info["files_total"] = len(candidate["files"])
    _persist_music_job(download_id)
    await _slskd_download_files(candidate["peer_username"], candidate["files"])
    info["status"] = "downloading"
    info["updated_at"] = time.time()
    _persist_music_job(download_id)
    asyncio.create_task(_music_start_check(download_id, delay_seconds=25))
    asyncio.create_task(_poll_and_enrich(download_id, candidate["peer_username"], len(candidate["files"])))


async def _try_next_music_candidate(download_id: str, failed_reason: str) -> bool:
    info = _downloads.get(download_id)
    if not info or _has_meaningful_music_transfer(info):
        return False

    queue = info.get("candidate_queue") or []
    next_pos = (info.get("candidate_position") or 0) + 1
    if next_pos >= len(queue):
        return False

    current = queue[info.get("candidate_position") or 0]
    next_candidate = dict(queue[next_pos])
    next_candidate["attempt_number"] = next_pos + 1
    info["candidate_position"] = next_pos
    info.setdefault("attempt_history", []).append(
        {
            "result_index": current.get("result_index"),
            "peer": current.get("peer_username"),
            "reason": failed_reason,
            "failed_at": time.time(),
        }
    )
    info["message"] = failed_reason
    info["updated_at"] = time.time()
    _persist_music_job(download_id)

    title = _music_title(info)
    await send_telegram_message(
        f"{title} could not start with peer {current.get('peer_username')}: {failed_reason} "
        f"Trying fallback result {next_candidate.get('result_index')} from peer {next_candidate.get('peer_username')}."
    )
    await _activate_music_candidate(download_id, next_candidate)
    return True

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
    fallback_result_indices: list[int] = []
    language: str          # "english" | "hindi" | "punjabi"


class MusicImportRequest(BaseModel):
    source_path: str
    language: str
    mode: str = "auto"   # "auto" | "album" | "track"
    replace_existing: bool = False


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


def _is_disc_dir(name: str) -> bool:
    return bool(re.fullmatch(r"(cd|disc|disk)\s*[_ -]*\d+", name.strip(), re.IGNORECASE))


def _resolve_manual_import(source_path: str, requested_mode: str) -> tuple[str, Path, list[Path]]:
    source = Path(source_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail=f"Source path not found: {source_path}")

    if source.is_file():
        if source.suffix.lower() != ".flac":
            raise HTTPException(status_code=400, detail="Manual music import only accepts .flac files or folders containing .flac files")
        return "track", source, [source]

    flac_files = sorted(source.rglob("*.flac"))
    if not flac_files:
        raise HTTPException(status_code=400, detail="No .flac files found under source_path")

    if requested_mode == "track":
        if len(flac_files) != 1:
            raise HTTPException(status_code=400, detail="mode=track requires exactly one FLAC file")
        return "track", flac_files[0], flac_files

    if requested_mode == "album":
        return "album", source, flac_files

    has_disc_dirs = any(_is_disc_dir(p.name) for p in {f.parent for f in flac_files})
    mode = "album" if len(flac_files) > 1 or has_disc_dirs else "track"
    resolved_source = source if mode == "album" else flac_files[0]
    return mode, resolved_source, flac_files


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

    return sorted(folders.values(), key=lambda x: (x["best_tier"], -x["total_size"]))[:25]


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
    return sorted(tracks, key=lambda x: (x["best_tier"], -x["size_mb"]))[:25]


# ---------------------------------------------------------------------------
# slskd search
# ---------------------------------------------------------------------------

async def _slskd_search(query: str, timeout_ms: int = 30000) -> list:
    """Run a slskd search and return ranked result list."""
    search_id = str(uuid.uuid4())
    hdrs = await _slskd_headers()

    async with httpx.AsyncClient(timeout=45) as client:
        await client.post(
            f"{settings.SLSKD_URL}/api/v0/searches",
            headers=hdrs,
            json={
                "id": search_id,
                "searchText": query,
                "fileLimit": 10000,
                "responseLimit": 500,
                "filterResponses": False,
                "timeout": timeout_ms,
            },
        )

        # Poll until Completed/Stopped (max 45s — covers 30s timeout with headroom)
        for _ in range(45):
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


_SIZE_MISMATCH_RE = re.compile(r"remote size of (\d+)")


async def _slskd_download_files(peer_username: str, file_list: list[dict]) -> None:
    """Queue all files from a peer in a single batch POST.

    Handles TransferSizeMismatchException transparently: after enqueueing,
    waits 2 s, checks for immediate size-mismatch failures, then deletes
    and re-enqueues with the peer's actual announced size.
    """
    hdrs = await _slskd_headers()
    payload = [{"filename": f["filename"], "size": f.get("size") or 0} for f in file_list]

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{settings.SLSKD_URL}/api/v0/transfers/downloads/{peer_username}",
            headers=hdrs,
            json=payload,
        )

    try:
        resp_data = r.json()
        if isinstance(resp_data, dict):
            enqueued = len(resp_data.get("enqueued", []))
            n_failed = len(resp_data.get("failed", []))
        else:
            enqueued = n_failed = 0
            logger.warning("slskd enqueue unexpected response: %s", resp_data)
    except Exception:
        enqueued = n_failed = 0
    logger.info("slskd enqueue %s: HTTP %s, enqueued=%d failed=%d",
                peer_username, r.status_code, enqueued, n_failed)

    # Wait briefly for any immediate size-mismatch failures to appear
    await asyncio.sleep(2)
    await _slskd_fix_size_mismatches(peer_username, file_list)


async def _slskd_fix_size_mismatches(peer_username: str, file_list: list[dict]) -> None:
    """Check for failed transfers due to size mismatch; delete and re-enqueue with corrected sizes."""
    filenames = {f["filename"] for f in file_list}
    hdrs = await _slskd_headers()

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{settings.SLSKD_URL}/api/v0/transfers/downloads/{peer_username}",
                headers=hdrs,
            )
        if r.status_code != 200:
            return
        data = r.json() or {}
    except Exception as e:
        logger.warning("Size-mismatch check failed: %s", e)
        return

    retry_payload = []
    for directory in (data.get("directories") or []):
        for tf in (directory.get("files") or []):
            if tf.get("filename") not in filenames:
                continue
            exception = tf.get("exception") or ""
            m = _SIZE_MISMATCH_RE.search(exception)
            if not m:
                continue
            actual_size = int(m.group(1))
            transfer_id = tf.get("id")
            logger.info("Size mismatch %s: expected %d → actual %d — retrying",
                        tf["filename"].rsplit("\\", 1)[-1], tf.get("size", 0), actual_size)
            if transfer_id:
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.delete(
                            f"{settings.SLSKD_URL}/api/v0/transfers/downloads/{peer_username}/{transfer_id}",
                            headers=hdrs,
                            params={"remove": "true"},
                        )
                except Exception as e:
                    logger.warning("Delete failed transfer %s: %s", transfer_id, e)
            retry_payload.append({"filename": tf["filename"], "size": actual_size})

    if retry_payload:
        try:
            hdrs = await _slskd_headers()
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{settings.SLSKD_URL}/api/v0/transfers/downloads/{peer_username}",
                    headers=hdrs,
                    json=retry_payload,
                )
            logger.info("Size-corrected re-enqueue %s: HTTP %s, files=%d",
                        peer_username, r.status_code, len(retry_payload))
        except Exception as e:
            logger.error("Size-corrected re-enqueue failed: %s", e)


async def _poll_and_enrich(download_id: str, peer_username: str, file_count: int) -> None:
    """Monitor slskd until all files complete, then run enrichment."""
    logger.info("Download poll: %s (%s, %d files)", download_id, peer_username, file_count)
    info = _downloads.get(download_id)
    if not info:
        return

    _downloads[download_id]["status"] = "downloading"
    _downloads[download_id]["updated_at"] = time.time()
    _persist_music_job(download_id)

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
            total_bytes = int(info.get("bytes_total") or 0)
            elapsed = max(time.monotonic() - _start, 1)
            speed = max(_cur_total_bytes - _last_bytes, 0) / 10
            remaining = max(total_bytes - _cur_total_bytes, 0) if total_bytes else None
            eta = int(remaining / speed) if remaining is not None and speed > 0 else None
            _downloads[download_id]["bytes_done"] = _cur_total_bytes
            _downloads[download_id]["files_done"] = completed
            _downloads[download_id]["files_total"] = file_count
            _downloads[download_id]["speed_bytes_per_second"] = round(speed, 1) if speed else None
            _downloads[download_id]["eta_seconds"] = eta
            _downloads[download_id]["progress_percent"] = round((_cur_total_bytes / total_bytes) * 100, 1) if total_bytes else None
            _downloads[download_id]["updated_at"] = time.time()
            _persist_music_job(download_id)
            await _maybe_send_music_progress_confirm(download_id)
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
                    failure_message = f"Peer {peer_username} was {reason}. Download cancelled."
                    if await _try_next_music_candidate(download_id, failure_message):
                        return
                    _downloads[download_id]["status"]  = "stuck"
                    _downloads[download_id]["message"] = failure_message
                    _downloads[download_id]["updated_at"] = time.time()
                    _persist_music_job(download_id)
                    await _send_music_notification_once(
                        download_id,
                        "terminal",
                        f"Music download failed for {_music_title(_downloads[download_id])}: {failure_message}",
                    )
                    return
        except Exception as e:
            logger.warning("Transfer poll error: %s", e)

    if _downloads[download_id].get("status") != "downloading":
        return  # was marked stuck inside the loop
    if completed == 0:
        failure_message = f"Peer {peer_username} rejected or failed the transfer."
        if await _try_next_music_candidate(download_id, failure_message):
            return
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = failure_message
        _downloads[download_id]["updated_at"] = time.time()
        _persist_music_job(download_id)
        await _send_music_notification_once(
            download_id,
            "terminal",
            f"Music download failed for {_music_title(_downloads[download_id])}: {failure_message}",
        )
        return
    _downloads[download_id]["status"] = "enriching"
    _downloads[download_id]["progress_percent"] = 100.0
    _downloads[download_id]["updated_at"] = time.time()
    _persist_music_job(download_id)

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

    try:
        result = await enrich_and_deliver(
            download_folder=local_folder,
            language=info["language"],
            artist_hint=info.get("artist", ""),
            album_hint=info.get("album", ""),
        )
    except Exception as e:
        logger.exception("Album enrichment crashed for %s", local_folder)
        _downloads[download_id]["status"] = "failed"
        _downloads[download_id]["message"] = f"Enrichment crashed: {e}"
        _downloads[download_id]["updated_at"] = time.time()
        _persist_music_job(download_id)
        await _send_music_notification_once(
            download_id,
            "terminal",
            f"Music download failed for {_music_title(_downloads[download_id])}: Enrichment crashed: {e}",
        )
        return
    if not result.get("success"):
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = result.get("message") or "Enrichment failed — album could not be identified."
        _downloads[download_id]["updated_at"] = time.time()
        _persist_music_job(download_id)
        await _send_music_notification_once(
            download_id,
            "terminal",
            f"Music download failed for {_music_title(_downloads[download_id])}: {_downloads[download_id]['message']}",
        )
        return
    if result.get("message"):
        _downloads[download_id]["message"] = result["message"]
    _downloads[download_id]["status"] = "done"
    _downloads[download_id]["updated_at"] = time.time()
    _persist_music_job(download_id)
    destination = result.get("destination")
    if destination:
        text = f"{Path(destination).name} finished importing. Navidrome scan was triggered."
    elif result.get("duplicate"):
        text = f"{_music_title(_downloads[download_id])} was already present in Navidrome. No re-import was needed."
    else:
        text = f"{_music_title(_downloads[download_id])} finished importing."
    if result.get("message"):
        text += f" {result['message']}"
    await _send_music_notification_once(download_id, "terminal", text)


async def _poll_and_enrich_track(download_id: str, peer_username: str, filename: str) -> None:
    """Monitor slskd until a single file completes, then run single-track enrichment."""
    logger.info("Track poll: %s (%s, %s)", download_id, peer_username, filename)
    info = _downloads.get(download_id)
    if not info:
        return

    _downloads[download_id]["status"] = "downloading"
    _downloads[download_id]["updated_at"] = time.time()
    _persist_music_job(download_id)

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
            total_bytes = int(info.get("bytes_total") or 0)
            speed = max(_cur_bytes - _last_bytes, 0) / 10
            remaining = max(total_bytes - _cur_bytes, 0) if total_bytes else None
            eta = int(remaining / speed) if remaining is not None and speed > 0 else None
            _downloads[download_id]["bytes_done"] = _cur_bytes
            _downloads[download_id]["files_done"] = completed
            _downloads[download_id]["files_total"] = 1
            _downloads[download_id]["speed_bytes_per_second"] = round(speed, 1) if speed else None
            _downloads[download_id]["eta_seconds"] = eta
            _downloads[download_id]["progress_percent"] = round((_cur_bytes / total_bytes) * 100, 1) if total_bytes else None
            _downloads[download_id]["updated_at"] = time.time()
            _persist_music_job(download_id)
            await _maybe_send_music_progress_confirm(download_id)
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
                    failure_message = f"Peer {peer_username} was {reason}. Download cancelled."
                    if await _try_next_music_candidate(download_id, failure_message):
                        return
                    _downloads[download_id]["status"]  = "stuck"
                    _downloads[download_id]["message"] = failure_message
                    _downloads[download_id]["updated_at"] = time.time()
                    _persist_music_job(download_id)
                    await _send_music_notification_once(
                        download_id,
                        "terminal",
                        f"Music download failed for {_music_title(_downloads[download_id])}: {failure_message}",
                    )
                    return
        except Exception as e:
            logger.warning("Track poll error: %s", e)

    if _downloads[download_id].get("status") != "downloading":
        return  # was marked stuck inside the loop
    if completed == 0:
        failure_message = f"Peer {peer_username} rejected or failed the transfer."
        if await _try_next_music_candidate(download_id, failure_message):
            return
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = failure_message
        _downloads[download_id]["updated_at"] = time.time()
        _persist_music_job(download_id)
        await _send_music_notification_once(
            download_id,
            "terminal",
            f"Music download failed for {_music_title(_downloads[download_id])}: {failure_message}",
        )
        return
    _downloads[download_id]["status"] = "enriching"
    _downloads[download_id]["progress_percent"] = 100.0
    _downloads[download_id]["updated_at"] = time.time()
    _persist_music_job(download_id)

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

    try:
        result = await enrich_single_track(
            flac_path=local_path,
            language=info["language"],
            title_hint=info.get("title", ""),
            artist_hint=info.get("artist", ""),
        )
    except Exception as e:
        logger.exception("Track enrichment crashed for %s", local_path)
        _downloads[download_id]["status"] = "failed"
        _downloads[download_id]["message"] = f"Enrichment crashed: {e}"
        _downloads[download_id]["updated_at"] = time.time()
        _persist_music_job(download_id)
        await _send_music_notification_once(
            download_id,
            "terminal",
            f"Music download failed for {_music_title(_downloads[download_id])}: Enrichment crashed: {e}",
        )
        return
    if not result.get("success"):
        _downloads[download_id]["status"]  = "stuck"
        _downloads[download_id]["message"] = result.get("message") or "Enrichment failed — track could not be identified."
        _downloads[download_id]["updated_at"] = time.time()
        _persist_music_job(download_id)
        await _send_music_notification_once(
            download_id,
            "terminal",
            f"Music download failed for {_music_title(_downloads[download_id])}: {_downloads[download_id]['message']}",
        )
        return
    if result.get("message"):
        _downloads[download_id]["message"] = result["message"]
    _downloads[download_id]["status"] = "done"
    _downloads[download_id]["updated_at"] = time.time()
    _persist_music_job(download_id)
    if result.get("destination"):
        text = f"{Path(result['destination']).stem} finished importing. Navidrome scan was triggered."
        if result.get("message"):
            text += f" {result['message']}"
        await _send_music_notification_once(download_id, "terminal", text)


async def _run_manual_import(download_id: str, source_path: str, language: str, mode: str, replace_existing: bool = False) -> None:
    _downloads[download_id]["status"] = "enriching"
    _downloads[download_id]["updated_at"] = time.time()
    _downloads[download_id]["progress_percent"] = None
    _persist_music_job(download_id)
    try:
        if mode == "track":
            result = await enrich_single_track(
                flac_path=source_path,
                language=language,
                replace_existing=replace_existing,
            )
        else:
            result = await enrich_and_deliver(
                download_folder=source_path,
                language=language,
                replace_existing=replace_existing,
            )
        _downloads[download_id]["status"] = "done" if result.get("success") else "failed"
        if result.get("language"):
            _downloads[download_id]["language"] = result["language"]
        if result.get("message"):
            _downloads[download_id]["message"] = result["message"]
        if result.get("duplicate"):
            _downloads[download_id]["duplicate"] = True
        _downloads[download_id]["updated_at"] = time.time()
        _persist_music_job(download_id)
        if result.get("success") and result.get("destination"):
            text = f"{Path(result['destination']).name} manual music import finished. Navidrome scan was triggered."
            if result.get("message"):
                text += f" {result['message']}"
            await _send_music_notification_once(download_id, "terminal", text)
        elif not result.get("success"):
            await _send_music_notification_once(
                download_id,
                "terminal",
                f"Manual music import failed for {_music_title(_downloads[download_id])}: {_downloads[download_id].get('message') or 'unknown error'}",
            )
    except Exception as exc:
        logger.exception("Manual import failed for %s", source_path)
        _downloads[download_id]["status"] = "failed"
        _downloads[download_id]["message"] = str(exc)
        _downloads[download_id]["updated_at"] = time.time()
        _persist_music_job(download_id)
        await _send_music_notification_once(
            download_id,
            "terminal",
            f"Manual music import failed for {_music_title(_downloads[download_id])}: {exc}",
        )
    finally:
        if "/manual_imports/" in source_path:
            try:
                p = Path(source_path)
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.exists():
                    p.unlink(missing_ok=True)
                parent = p.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except Exception as cleanup_exc:
                logger.warning("Manual import cleanup failed for %s: %s", source_path, cleanup_exc)


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

    requested_indices = [req.result_index, *req.fallback_result_indices]
    ordered_indices: list[int] = []
    for one_based in requested_indices:
        if one_based not in ordered_indices:
            ordered_indices.append(one_based)
    for one_based in ordered_indices:
        if one_based < 1 or one_based > len(results_raw):
            raise HTTPException(status_code=400, detail=f"fallback result index {one_based} must be 1â€“{len(results_raw)}")

    candidate_queue: list[dict] = []
    for pos, one_based in enumerate(ordered_indices, start=1):
        result = results_raw[one_based - 1]
        peer = result["peer_username"]
        if mode == "track":
            filename = result["filename"]
            file_size = int(result["size_mb"] * 1_048_576)
            file_list = [{"filename": filename, "size": file_size}]
            candidate_queue.append(
                {
                    "mode": "track",
                    "result_index": one_based,
                    "attempt_number": pos,
                    "peer_username": peer,
                    "files": file_list,
                    "filename": filename,
                    "title": result.get("file_basename", "").rsplit(".", 1)[0],
                    "artist": "",
                    "bytes_total": file_size,
                    "quality_label": result["quality_label"],
                }
            )
        else:
            files = result["files"]
            candidate_queue.append(
                {
                    "mode": "album",
                    "result_index": one_based,
                    "attempt_number": pos,
                    "peer_username": peer,
                    "files": files,
                    "folder_path": result["folder_path"],
                    "artist": "",
                    "album": "",
                    "bytes_total": sum(int(f.get("size") or 0) for f in files),
                    "quality_label": result["quality_label"],
                }
            )

    first_candidate = candidate_queue[0]
    download_id = str(uuid.uuid4())

    if mode == "track":
        _downloads[download_id] = {
            "status": "starting",
            "language": req.language.lower(),
            "peer_username": first_candidate["peer_username"],
            "files": first_candidate["files"],
            "filename": first_candidate["filename"],
            "title": first_candidate["title"],
            "artist": "",
            "bytes_done": 0,
            "bytes_total": first_candidate["bytes_total"],
            "progress_percent": 0.0,
            "speed_bytes_per_second": None,
            "eta_seconds": None,
            "files_done": 0,
            "files_total": 1,
            "candidate_queue": candidate_queue,
            "candidate_position": 0,
            "attempt_history": [],
            "active_result_index": first_candidate["result_index"],
            "attempt_number": 1,
            "updated_at": time.time(),
            "started_at": time.time(),
            "start_notification_sent": False,
            "progress_notification_sent": False,
            "terminal_notification_sent": False,
        }
        _persist_music_job(download_id)
        await _activate_music_candidate(download_id, first_candidate)

        return {
            "success": True,
            "download_id": download_id,
            "files": 1,
            "peer": first_candidate["peer_username"],
            "track": first_candidate["title"],
            "quality": first_candidate["quality_label"],
            "language": req.language,
            "destination": "Misc/",
            "retry_chain": ordered_indices,
        }

    else:
        _downloads[download_id] = {
            "status": "starting",
            "language": req.language.lower(),
            "peer_username": first_candidate["peer_username"],
            "files": first_candidate["files"],
            "folder_path": first_candidate["folder_path"],
            "artist": "",
            "album": "",
            "bytes_done": 0,
            "bytes_total": first_candidate["bytes_total"],
            "progress_percent": 0.0,
            "speed_bytes_per_second": None,
            "eta_seconds": None,
            "files_done": 0,
            "files_total": len(first_candidate["files"]),
            "candidate_queue": candidate_queue,
            "candidate_position": 0,
            "attempt_history": [],
            "active_result_index": first_candidate["result_index"],
            "attempt_number": 1,
            "updated_at": time.time(),
            "started_at": time.time(),
            "start_notification_sent": False,
            "progress_notification_sent": False,
            "terminal_notification_sent": False,
        }
        _persist_music_job(download_id)
        await _activate_music_candidate(download_id, first_candidate)

        return {
            "success": True,
            "download_id": download_id,
            "files": len(first_candidate["files"]),
            "peer": first_candidate["peer_username"],
            "quality": first_candidate["quality_label"],
            "language": req.language,
            "retry_chain": ordered_indices,
        }


@router.post("/import")
async def music_import(req: MusicImportRequest, _: str = Depends(_require_api_key)):
    """Import a local FLAC file or folder that already exists on the VPS filesystem."""
    mode = req.mode if req.mode in ("auto", "album", "track") else "auto"
    resolved_mode, resolved_source, flac_files = _resolve_manual_import(req.source_path, mode)
    download_id = str(uuid.uuid4())

    _downloads[download_id] = {
        "status": "starting",
        "language": req.language.lower(),
        "peer_username": "manual-import",
        "mode": resolved_mode,
        "source_path": str(resolved_source),
        "files": [{"filename": str(p), "size": p.stat().st_size} for p in flac_files],
        "replace_existing": req.replace_existing,
        "bytes_done": None,
        "bytes_total": None,
        "progress_percent": None,
        "speed_bytes_per_second": None,
        "eta_seconds": None,
        "files_done": 0,
        "files_total": len(flac_files),
        "updated_at": time.time(),
        "started_at": time.time(),
        "start_notification_sent": False,
        "progress_notification_sent": False,
        "terminal_notification_sent": False,
    }
    _persist_music_job(download_id)

    asyncio.create_task(
        _run_manual_import(
            download_id=download_id,
            source_path=str(resolved_source),
            language=req.language.lower(),
            mode=resolved_mode,
            replace_existing=req.replace_existing,
        )
    )

    return {
        "success": True,
        "download_id": download_id,
        "mode": resolved_mode,
        "files": len(flac_files),
        "language": req.language.lower(),
        "source_path": str(resolved_source),
        "replace_existing": req.replace_existing,
    }


@router.get("/status/{download_id}")
async def music_status(download_id: str, _: str = Depends(_require_api_key)):
    """Poll download + enrichment status."""
    info = _downloads.get(download_id)
    if not info:
        stored = db_get_job(download_id)
        if not stored:
            raise HTTPException(status_code=404, detail="Download ID not found")
        return {
            "download_id": stored["job_id"],
            "status": stored["status"],
            "language": None,
            "peer": None,
            "message": stored.get("message"),
            "progress_percent": stored.get("progress_percent"),
            "bytes_done": stored.get("bytes_done"),
            "bytes_total": stored.get("bytes_total"),
            "speed_bytes_per_second": stored.get("speed_bytes_per_second"),
            "eta_seconds": stored.get("eta_seconds"),
            "files_done": stored.get("files_done"),
            "files_total": stored.get("files_total"),
            "updated_at": stored.get("updated_at"),
            "active_result_index": (stored.get("payload") or {}).get("active_result_index"),
            "retry_chain": (stored.get("payload") or {}).get("retry_chain"),
            "attempt_history": (stored.get("payload") or {}).get("attempt_history"),
        }
    resp: dict = {
        "download_id": download_id,
        "status": info.get("status"),
        "language": info.get("language"),
        "peer": info.get("peer_username"),
    }
    if info.get("message"):
        resp["message"] = info["message"]
    for key in ("progress_percent", "bytes_done", "bytes_total", "speed_bytes_per_second", "eta_seconds", "files_done", "files_total", "updated_at"):
        if key in info:
            resp[key] = info.get(key)
    resp["active_result_index"] = info.get("active_result_index")
    resp["retry_chain"] = [c.get("result_index") for c in (info.get("candidate_queue") or [])]
    resp["attempt_history"] = info.get("attempt_history") or []
    return resp
