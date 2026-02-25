import time
from typing import Optional

import httpx


class QBittorrentClient:
    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self._sid: Optional[str] = None
        self._auth_time: float = 0.0
        self._auth_ttl: float = 25 * 60  # refresh SID after 25 minutes

    async def _ensure_auth(self) -> None:
        now = time.monotonic()
        if self._sid and (now - self._auth_time) < self._auth_ttl:
            return

        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                f"{self.url}/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
                timeout=15,
            )
            resp.raise_for_status()
            if resp.text.strip().lower() != "ok.":
                raise RuntimeError(f"qBittorrent login failed: {resp.text!r}")
            sid = resp.cookies.get("SID") or client.cookies.get("SID")
            if not sid:
                raise RuntimeError("qBittorrent login succeeded but no SID cookie returned")
            self._sid = sid
        self._auth_time = now

    def _auth_cookies(self) -> dict:
        return {"SID": self._sid}

    async def add_torrent_from_url(
        self, torrent_url: str, save_path: str, category: str, tags: str = ""
    ) -> dict:
        # Download the .torrent file bytes (auth token in URL handles tracker auth)
        async with httpx.AsyncClient(follow_redirects=True) as dl_client:
            torrent_resp = await dl_client.get(torrent_url)
            torrent_resp.raise_for_status()
            torrent_bytes = torrent_resp.content

        await self._ensure_auth()

        post_data: dict = {"savepath": save_path, "category": category}
        if tags:
            post_data["tags"] = tags

        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.post(
                f"{self.url}/api/v2/torrents/add",
                cookies=self._auth_cookies(),
                files={"torrents": ("download.torrent", torrent_bytes, "application/x-bittorrent")},
                data=post_data,
            )

            if resp.status_code == 403:
                # SID expired — re-auth once and retry
                self._sid = None
                await self._ensure_auth()
                resp = await client.post(
                    f"{self.url}/api/v2/torrents/add",
                    cookies=self._auth_cookies(),
                    files={"torrents": ("download.torrent", torrent_bytes, "application/x-bittorrent")},
                    data=post_data,
                )

            resp.raise_for_status()
            if resp.text.strip().lower() == "fails.":
                raise RuntimeError(
                    "qBittorrent rejected the torrent — possibly a duplicate or invalid save path"
                )
            return {"success": True, "qbt_response": resp.text}

    async def get_torrent_tags(self, info_hash: str) -> tuple[str, Optional[int]]:
        """Look up the tag we stored at add time to retrieve clean title and year.

        Tags are stored as "Movie Title|Year" (e.g. "RoboCop 2|1990").
        Returns (title, year) — year is None if not stored.
        """
        await self._ensure_auth()
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(
                f"{self.url}/api/v2/torrents/info",
                params={"hashes": info_hash.lower()},
                cookies=self._auth_cookies(),
            )
            resp.raise_for_status()
            torrents = resp.json()

        if not torrents:
            raise ValueError(f"Torrent {info_hash} not found in qBittorrent")

        t = torrents[0]
        tags_str = t.get("tags", "")
        parts = [p.strip() for p in tags_str.split("|", 1)]
        title = parts[0] if parts[0] else t.get("name", "")
        year: Optional[int] = None
        if len(parts) > 1:
            try:
                year = int(parts[1])
            except ValueError:
                pass
        return title, year

    async def get_active_downloads(self) -> list[dict]:
        await self._ensure_auth()

        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(
                f"{self.url}/api/v2/torrents/info",
                params={"filter": "active"},
                cookies=self._auth_cookies(),
            )

            if resp.status_code == 403:
                self._sid = None
                await self._ensure_auth()
                resp = await client.get(
                    f"{self.url}/api/v2/torrents/info",
                    params={"filter": "active"},
                    cookies=self._auth_cookies(),
                )

            resp.raise_for_status()

        torrents = resp.json()
        result = []
        for t in torrents:
            eta_secs = t.get("eta", 0)
            if eta_secs < 0 or eta_secs > 86400 * 30:
                eta_str = "unknown"
            else:
                h, rem = divmod(eta_secs, 3600)
                m, s = divmod(rem, 60)
                eta_str = f"{h:02d}:{m:02d}:{s:02d}"

            speed_bps = t.get("dlspeed", 0)
            speed_mbs = speed_bps / (1024 * 1024)

            result.append(
                {
                    "name": t.get("name", ""),
                    "progress": round(t.get("progress", 0) * 100, 1),
                    "speed": f"{speed_mbs:.1f} MB/s",
                    "eta": eta_str,
                    "state": t.get("state", "unknown"),
                }
            )
        return result
