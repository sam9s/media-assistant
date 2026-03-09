"""AzuraCast radio helpers."""
import asyncio
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


def _normalize_station_path(path: str) -> str:
    base = Path(path or "").name.lower()
    stem = Path(base).stem
    suffix = Path(base).suffix.lower()
    stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return f"{stem}{suffix}"


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


def _azuracast_headers() -> dict[str, str]:
    if not settings.AZURACAST_API_KEY:
        raise HTTPException(status_code=500, detail="AzuraCast API key is not configured")
    return {"Authorization": f"Bearer {settings.AZURACAST_API_KEY}"}


def _station_files_url() -> str:
    base = settings.AZURACAST_URL.rstrip("/")
    return f"{base}/api/station/{settings.AZURACAST_STATION_ID}/files"


def _station_upload_url() -> str:
    return f"{_station_files_url()}/upload"


def _station_file_url(file_id: int | str) -> str:
    base = settings.AZURACAST_URL.rstrip("/")
    return f"{base}/api/station/{settings.AZURACAST_STATION_ID}/file/{file_id}"


async def _list_station_files(client: httpx.AsyncClient) -> list[dict]:
    resp = await client.get(_station_files_url(), headers=_azuracast_headers())
    resp.raise_for_status()
    payload = resp.json()
    return payload if isinstance(payload, list) else []


async def _find_station_files_by_path(client: httpx.AsyncClient, path: str) -> list[dict]:
    wanted = _normalize_station_path(path)
    files = await _list_station_files(client)
    return [
        item
        for item in files
        if _normalize_station_path(item.get("path") or "") == wanted
    ]


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

    dest_name = _safe_mp3_name(file.filename)
    replace_existing = _coerce_bool(replace)

    try:
        file_bytes = await file.read()
    finally:
        await file.close()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded MP3 is empty")

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            existing = await _find_station_files_by_path(client, dest_name)

            if existing and not replace_existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"File already exists in AzuraCast media library: {dest_name}",
                )

            if existing and replace_existing:
                for item in existing:
                    delete_resp = await client.delete(
                        _station_file_url(item["id"]),
                        headers=_azuracast_headers(),
                    )
                    delete_resp.raise_for_status()

            upload_resp = await client.post(
                _station_upload_url(),
                headers=_azuracast_headers(),
                files={"file": (dest_name, file_bytes, "audio/mpeg")},
            )
            upload_resp.raise_for_status()

            created: list[dict] = []
            for _ in range(10):
                created = await _find_station_files_by_path(client, dest_name)
                if created:
                    break
                await asyncio.sleep(1)
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AzuraCast upload failed: {exc.response.status_code} {exc.response.text[:300]}",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AzuraCast upload failed: {exc}")

    if not created:
        raise HTTPException(
            status_code=502,
            detail="AzuraCast upload succeeded but the file was not indexed immediately after upload",
        )

    created_record = created[-1]

    size_bytes = len(file_bytes)
    return {
        "success": True,
        "saved_to": f"{settings.RADIO_LIBRARY_PATH.rstrip('/')}/{dest_name}",
        "filename": dest_name,
        "size_mb": round(size_bytes / (1024 * 1024), 2),
        "azuracast_file_id": created_record.get("id"),
        "azuracast_path": created_record.get("path"),
        "station_shortcode": settings.AZURACAST_STATION_SHORTCODE,
        "replace": replace_existing,
        "native_upload": True,
        "scan_triggered": True,
        "scan_mode": "azuracast native upload api",
        "message": "MP3 uploaded directly into AzuraCast station media and indexed successfully.",
    }
