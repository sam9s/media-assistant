from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Security, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field

from app.config import settings
from app.immich import ImmichClient
from app.notifications import send_telegram_message

router = APIRouter(prefix="/photos", tags=["photos"])

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: Optional[str] = Security(_api_key_header)) -> str:
    if key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key or ""


def _require_immich() -> ImmichClient:
    if not settings.IMMICH_API_KEY:
        raise HTTPException(status_code=500, detail="Immich API key is not configured")
    return ImmichClient(settings.IMMICH_URL, settings.IMMICH_API_KEY)


def _safe_upload_name(filename: str) -> str:
    name = Path(filename or "upload.bin").name
    return name or "upload.bin"


class PhotoAlbumCreateRequest(BaseModel):
    album_name: str = Field(..., min_length=1)
    description: str = ""
    asset_ids: list[str] = Field(default_factory=list)


class PhotoAlbumUpdateRequest(BaseModel):
    album_name: Optional[str] = None
    description: Optional[str] = None


class PhotoAlbumAssetsRequest(BaseModel):
    asset_ids: list[str] = Field(..., min_length=1)


class PhotoSearchRequest(BaseModel):
    original_file_name: Optional[str] = None
    album_ids: list[str] = Field(default_factory=list)
    page: int = 1
    size: int = 50
    with_deleted: bool = False


class PhotoAssetUpdateRequest(BaseModel):
    original_file_name: Optional[str] = None
    description: Optional[str] = None
    is_favorite: Optional[bool] = None


@router.get("/albums")
async def list_albums(
    name: Optional[str] = Query(default=None),
    _: str = Depends(_require_api_key),
):
    client = _require_immich()
    try:
        albums = await client.get_albums()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Immich albums fetch failed: {exc.response.status_code} {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Immich albums fetch failed: {exc}")

    if name:
        want = name.strip().lower()
        albums = [a for a in albums if (a.get("albumName") or "").strip().lower() == want]

    return {"count": len(albums), "albums": albums}


@router.post("/albums")
async def create_album(
    body: PhotoAlbumCreateRequest,
    _: str = Depends(_require_api_key),
):
    client = _require_immich()
    try:
        album = await client.create_album(body.album_name, body.description, body.asset_ids)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Immich album create failed: {exc.response.status_code} {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Immich album create failed: {exc}")
    return {"success": True, "album": album}


@router.patch("/albums/{album_id}")
async def update_album(
    album_id: str,
    body: PhotoAlbumUpdateRequest,
    _: str = Depends(_require_api_key),
):
    client = _require_immich()
    try:
        album = await client.update_album(album_id, body.album_name, body.description)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Immich album update failed: {exc.response.status_code} {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Immich album update failed: {exc}")
    return {"success": True, "album": album}


@router.delete("/albums/{album_id}")
async def delete_album(
    album_id: str,
    _: str = Depends(_require_api_key),
):
    client = _require_immich()
    try:
        await client.delete_album(album_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Immich album delete failed: {exc.response.status_code} {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Immich album delete failed: {exc}")
    return {"success": True, "album_id": album_id}


@router.post("/albums/{album_id}/assets")
async def add_assets_to_album(
    album_id: str,
    body: PhotoAlbumAssetsRequest,
    _: str = Depends(_require_api_key),
):
    client = _require_immich()
    try:
        result = await client.add_assets_to_album(album_id, body.asset_ids)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Immich add-to-album failed: {exc.response.status_code} {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Immich add-to-album failed: {exc}")
    return {"success": True, "result": result}


@router.post("/search")
async def search_assets(
    body: PhotoSearchRequest,
    _: str = Depends(_require_api_key),
):
    client = _require_immich()
    try:
        payload = await client.search_assets(
            original_file_name=body.original_file_name,
            album_ids=body.album_ids or None,
            page=body.page,
            size=body.size,
            with_deleted=body.with_deleted,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Immich asset search failed: {exc.response.status_code} {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Immich asset search failed: {exc}")
    return payload


@router.patch("/assets/{asset_id}")
async def update_asset(
    asset_id: str,
    body: PhotoAssetUpdateRequest,
    _: str = Depends(_require_api_key),
):
    client = _require_immich()
    try:
        asset = await client.update_asset(
            asset_id,
            original_file_name=body.original_file_name,
            description=body.description,
            is_favorite=body.is_favorite,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Immich asset update failed: {exc.response.status_code} {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Immich asset update failed: {exc}")
    return {"success": True, "asset": asset}


@router.get("/assets/{asset_id}/download")
async def download_asset(
    asset_id: str,
    _: str = Depends(_require_api_key),
):
    client = _require_immich()
    try:
        file_bytes, content_type, content_disposition = await client.download_asset(asset_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Immich asset download failed: {exc.response.status_code} {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Immich asset download failed: {exc}")

    headers = {}
    if content_disposition:
        headers["Content-Disposition"] = content_disposition
    return StreamingResponse(iter([file_bytes]), media_type=content_type or "application/octet-stream", headers=headers)


@router.post("/upload")
async def upload_photo(
    file: UploadFile = File(...),
    album_id: Optional[str] = Form(default=None),
    album_name: Optional[str] = Form(default=None),
    is_favorite: bool = Form(default=False),
    _: str = Depends(_require_api_key),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    safe_name = _safe_upload_name(file.filename)
    try:
        file_bytes = await file.read()
    finally:
        await file.close()

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    client = _require_immich()
    now = datetime.now(UTC)
    try:
        upload = await client.upload_asset(
            safe_name,
            file_bytes,
            created_at=now,
            modified_at=now,
            is_favorite=is_favorite,
        )

        target_album = None
        if album_id:
            target_album = album_id
        elif album_name:
            albums = await client.get_albums()
            match = next((a for a in albums if (a.get("albumName") or "").strip().lower() == album_name.strip().lower()), None)
            if match:
                target_album = match.get("id")
            else:
                created = await client.create_album(album_name)
                target_album = created.get("id")

        album_result = None
        if target_album and upload.get("id"):
            album_result = await client.add_assets_to_album(target_album, [upload["id"]])

    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Immich upload failed: {exc.response.status_code} {exc.response.text[:300]}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Immich upload failed: {exc}")

    await send_telegram_message(
        f"{safe_name} uploaded to Immich{f' and added to album {album_name}' if album_name else ''}."
    )

    return {
        "success": True,
        "filename": safe_name,
        "asset": upload,
        "album_id": target_album,
        "album_result": album_result,
    }
