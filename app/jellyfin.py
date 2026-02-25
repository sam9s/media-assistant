from typing import Optional

import httpx


class JellyfinClient:
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.api_key = api_key

    async def refresh_library(self) -> None:
        """Trigger a full Jellyfin library scan so newly downloaded files appear immediately."""
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.post(
                f"{self.url}/Library/Refresh",
                params={"api_key": self.api_key},
            )
            resp.raise_for_status()

    async def search(self, title: str) -> dict:
        params = {
            "searchTerm": title,
            "IncludeItemTypes": "Movie,Series",
            "api_key": self.api_key,
            "Limit": 5,
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(f"{self.url}/Items", params=params)
            resp.raise_for_status()
            data = resp.json()

        items = data.get("Items", [])
        if not items:
            return {"found": False, "title": None, "year": None, "already_in_library": False}

        best = items[0]
        return {
            "found": True,
            "title": best.get("Name"),
            "year": best.get("ProductionYear"),
            "already_in_library": True,
        }
