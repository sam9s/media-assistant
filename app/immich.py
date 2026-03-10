from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx


class ImmichClient:
    def __init__(self, url: str, api_key: str) -> None:
        base = url.rstrip("/")
        self.base_url = base if base.endswith("/api") else f"{base}/api"
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key, "Accept": "application/json"}

    async def upload_asset(
        self,
        filename: str,
        file_bytes: bytes,
        created_at: datetime | None = None,
        modified_at: datetime | None = None,
        is_favorite: bool = False,
    ) -> dict[str, Any]:
        created = (created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        modified = (modified_at or created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        checksum = hashlib.sha1(file_bytes).hexdigest()
        device_id = "samassist"
        device_asset_id = f"samassist-{checksum[:16]}-{Path(filename).name}"

        data = {
            "deviceAssetId": device_asset_id,
            "deviceId": device_id,
            "fileCreatedAt": created,
            "fileModifiedAt": modified,
            "filename": Path(filename).name,
            "isFavorite": "true" if is_favorite else "false",
        }

        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.post(
                f"{self.base_url}/assets",
                headers={**self._headers(), "x-immich-checksum": checksum},
                data=data,
                files={"assetData": (Path(filename).name, file_bytes, content_type)},
            )
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, dict) else {}

    async def get_albums(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(f"{self.base_url}/albums", headers=self._headers())
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, list) else []

    async def create_album(
        self,
        album_name: str,
        description: str = "",
        asset_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"albumName": album_name}
        if description:
            body["description"] = description
        if asset_ids:
            body["assetIds"] = asset_ids
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.post(f"{self.base_url}/albums", headers=self._headers(), json=body)
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, dict) else {}

    async def update_album(self, album_id: str, album_name: str | None = None, description: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if album_name is not None:
            body["albumName"] = album_name
        if description is not None:
            body["description"] = description
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.patch(f"{self.base_url}/albums/{album_id}", headers=self._headers(), json=body)
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, dict) else {}

    async def delete_album(self, album_id: str) -> None:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.delete(f"{self.base_url}/albums/{album_id}", headers=self._headers())
            resp.raise_for_status()

    async def add_assets_to_album(self, album_id: str, asset_ids: list[str]) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.put(
                f"{self.base_url}/albums/{album_id}/assets",
                headers=self._headers(),
                json={"ids": asset_ids},
            )
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, list) else []

    async def search_assets(
        self,
        *,
        original_file_name: str | None = None,
        album_ids: list[str] | None = None,
        page: int = 1,
        size: int = 50,
        with_deleted: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "page": page,
            "size": size,
            "withDeleted": with_deleted,
        }
        if original_file_name:
            body["originalFileName"] = original_file_name
        if album_ids:
            body["albumIds"] = album_ids
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.post(f"{self.base_url}/search/metadata", headers=self._headers(), json=body)
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, dict) else {}

    async def update_asset(
        self,
        asset_id: str,
        *,
        original_file_name: str | None = None,
        description: str | None = None,
        is_favorite: bool | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if original_file_name is not None:
            body["originalFileName"] = original_file_name
        if description is not None:
            body["description"] = description
        if is_favorite is not None:
            body["isFavorite"] = is_favorite
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.patch(f"{self.base_url}/assets/{asset_id}", headers=self._headers(), json=body)
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, dict) else {}

    async def download_asset(self, asset_id: str) -> tuple[bytes, str | None, str | None]:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(f"{self.base_url}/assets/{asset_id}/original", headers=self._headers())
            resp.raise_for_status()
            return resp.content, resp.headers.get("content-type"), resp.headers.get("content-disposition")
