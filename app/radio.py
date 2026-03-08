"""
AzuraCast radio helpers.

Phase 1/2 scope:
  - expose public now-playing through our API
  - accept MP3 uploads into the shared radio library folder

The live AzuraCast station is expected to see files from the shared library path.
Immediate explicit rescan is not wired here yet; AzuraCast background sync handles it.
"""
import os
import re
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Security, UploadFile
from fastapi.security.api_key import APIKeyHeader

from app.config import settings

router = APIRouter(prefix="/radio", tags=["radio"])

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: str = Security(_api_key_header)) -> str:
    if key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key


def _safe_mp3_name(filename: str) -> str:
    name = Path(filename or "upload.mp3").name
    stem = Path(name).stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", stem).strip()
    stem = re.sub(r"\s{2,}", " ", stem)
    if not stem:
        stem = "radio_upload"
    return f"{stem}.mp3"


def _pick_station(payload: list[dict]) -> dict | None:
    if not payload:
        return None
    shortcode = settings.AZURACAST_STATION_SHORTCODE.strip().lower()
    for row in payload:
        station = row.get("station") or {}
        if (station.get("shortcode") or "").strip().lower() == shortcode:
            return row
    return payload[0]


def _coerce_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@router.get("/nowplaying")
async def radio_nowplaying(_: str = Depends(_require_api_key)):
    url = f"{settings.AZURACAST_URL.rstrip('/')}/api/nowplaying"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AzuraCast nowplaying failed: {exc}")

    station_row = _pick_station(payload if isinstance(payload, list) else [])
    if not station_row:
        return {"status": "ok", "station": None, "now_playing": None}

    station = station_row.get("station") or {}
    current = station_row.get("now_playing") or {}
    song = current.get("song") or {}
    listeners = station_row.get("listeners") or {}

    return {
        "status": "ok",
        "station": {
            "id": station.get("id"),
            "name": station.get("name"),
            "shortcode": station.get("shortcode"),
            "listen_url": station.get("listen_url"),
        },
        "listeners": {
            "total": listeners.get("total", 0),
            "unique": listeners.get("unique", 0),
            "current": listeners.get("current", 0),
        },
        "now_playing": {
            "title": song.get("title"),
            "artist": song.get("artist"),
            "album": song.get("album"),
            "art": song.get("art"),
            "played_at": current.get("played_at"),
            "duration": current.get("duration"),
            "playlist": current.get("playlist"),
            "is_request": current.get("is_request", False),
        },
    }


@router.post("/upload")
async def radio_upload(
    file: UploadFile = File(...),
    replace: str = Form("false"),
    _: str = Depends(_require_api_key),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    if Path(file.filename).suffix.lower() != ".mp3":
        raise HTTPException(status_code=400, detail="Only MP3 uploads are supported for radio")

    dest_dir = settings.RADIO_LIBRARY_PATH
    os.makedirs(dest_dir, exist_ok=True)

    dest_name = _safe_mp3_name(file.filename)
    dest_path = os.path.join(dest_dir, dest_name)

    replace_existing = _coerce_bool(replace)

    if os.path.exists(dest_path) and not replace_existing:
        raise HTTPException(
            status_code=409,
            detail=f"File already exists in radio library: {dest_name}",
        )

    try:
        with open(dest_path, "wb") as out_f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out_f.write(chunk)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {exc}")
    finally:
        await file.close()

    size_bytes = os.path.getsize(dest_path)
    return {
        "success": True,
        "saved_to": dest_path,
        "filename": dest_name,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "station_shortcode": settings.AZURACAST_STATION_SHORTCODE,
        "replace": replace_existing,
        "scan_triggered": False,
        "scan_mode": "azuracast scheduled sync",
        "message": "MP3 saved into the shared radio library. AzuraCast should ingest it on its scheduled media sync.",
    }
