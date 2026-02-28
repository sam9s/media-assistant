import time
from typing import Optional

import httpx


class KavitaClient:
    """
    Kavita API client — handles auth (JWT), library listing, search, and scan.
    Internal URL: http://localhost:8091 (host port 8091 → container port 5000)
    """

    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self._token: Optional[str] = None
        self._auth_time: float = 0.0
        self._auth_ttl: float = 20 * 60  # refresh token after 20 minutes

    async def _ensure_auth(self) -> None:
        now = time.monotonic()
        if self._token and (now - self._auth_time) < self._auth_ttl:
            return

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/api/Account/login",
                json={"username": self.username, "password": self.password},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data.get("token")
            if not self._token:
                raise RuntimeError(f"Kavita login failed — no token in response: {data}")
        self._auth_time = now

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def get_libraries(self) -> list[dict]:
        """Return all libraries with their IDs and names."""
        await self._ensure_auth()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.url}/api/Library/libraries",
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_library_id(self, name_contains: str) -> Optional[int]:
        """Find library ID by partial name match (case-insensitive)."""
        libraries = await self.get_libraries()
        name_lower = name_contains.lower()
        for lib in libraries:
            if name_lower in lib.get("name", "").lower():
                return lib["id"]
        return None

    async def scan_library(self, library_id: int) -> bool:
        """Trigger a library scan. Returns True if scan was queued successfully."""
        await self._ensure_auth()
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/api/Library/scan",
                params={"libraryId": library_id, "force": False},
                headers=self._headers(),
                timeout=10,
            )
            # Kavita returns 200 on success
            return resp.status_code == 200

    async def search(self, query: str) -> dict:
        """Search Kavita library. Returns series/chapter matches."""
        await self._ensure_auth()
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.url}/api/Search/search",
                params={"queryString": query},
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()

    async def is_in_library(self, title: str) -> bool:
        """Returns True if a title already exists in Kavita."""
        try:
            result = await self.search(title)
            series = result.get("series", [])
            return len(series) > 0
        except Exception:
            return False
