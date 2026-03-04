import asyncio
import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


class OpenSubtitlesClient:
    def __init__(
        self,
        api_key: str,
        username: str,
        password: str,
        languages: str = "en",
        prefer_sdh: bool = False,
        proxy_url: str = "",
        proxy_key: str = "",
    ):
        self.api_key = api_key
        self.username = username
        self.password = password
        self.languages = languages or "en"
        self.prefer_sdh = prefer_sdh
        self.proxy_url = (proxy_url or "").rstrip("/")
        self.proxy_key = proxy_key
        self._token: Optional[str] = None
        self._base_url = "https://api.opensubtitles.com"
        self._auth_time: float = 0.0
        self._auth_ttl: float = 20 * 60
        self._headers = {
            "Api-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "X-User-Agent": "SamAssist v2.2.0",
        }

    async def _ensure_auth(self) -> None:
        if self.proxy_url:
            return
        if not self.api_key or not self.username or not self.password:
            raise RuntimeError("OpenSubtitles credentials are not configured")

        now = time.monotonic()
        if self._token and (now - self._auth_time) < self._auth_ttl:
            return

        data = await asyncio.to_thread(
            self._request_json,
            "https://api.opensubtitles.com/api/v1/login",
            method="POST",
            payload={"username": self.username, "password": self.password},
        )
        token = data.get("token")
        if not token:
            raise RuntimeError(f"OpenSubtitles login succeeded but no token returned: {data}")
        base_url = str(data.get("base_url") or "api.opensubtitles.com").strip()
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._auth_time = now

    async def search_candidates(
        self,
        title: str,
        year: Optional[int],
        original_name: str = "",
        languages: Optional[str] = None,
    ) -> list[dict]:
        if self.proxy_url:
            payload = await asyncio.to_thread(
                self._proxy_json,
                "/search",
                {
                    "title": title,
                    "year": year,
                    "original_name": original_name,
                    "languages": languages or self.languages,
                },
            )
            return payload.get("candidates", [])

        await self._ensure_auth()
        lang = languages or self.languages

        queries = self._build_queries(title, year, original_name)
        seen_file_ids: set[int] = set()
        combined: list[dict] = []

        for query in queries:
            payload = await asyncio.to_thread(
                self._request_json,
                f"{self._base_url}/api/v1/subtitles",
                params={"query": query, "languages": lang},
                token=self._token,
            )
            for item in payload.get("data", []):
                for candidate in self._extract_candidates(item):
                    file_id = candidate.get("file_id")
                    if not file_id or file_id in seen_file_ids:
                        continue
                    seen_file_ids.add(file_id)
                    combined.append(candidate)

        release_hint = self._release_hint(original_name)
        title_hint = self._normalize_query(title)
        combined.sort(
            key=lambda item: self._candidate_score(
                item=item,
                release_hint=release_hint,
                title_hint=title_hint,
                year=year,
            ),
            reverse=True,
        )
        return combined

    async def download_subtitle(
        self,
        file_id: int,
        *,
        sub_format: str = "srt",
    ) -> tuple[bytes, str]:
        if self.proxy_url:
            payload = await asyncio.to_thread(
                self._proxy_json,
                "/download",
                {"file_id": file_id, "sub_format": sub_format},
            )
            content_b64 = payload.get("content_b64")
            if not content_b64:
                raise RuntimeError(f"OpenSubtitles proxy download response missing content: {payload}")
            filename = payload.get("file_name") or f"{file_id}.{sub_format}"
            return base64.b64decode(content_b64), filename

        await self._ensure_auth()

        payload = await asyncio.to_thread(
            self._request_json,
            f"{self._base_url}/api/v1/download",
            method="POST",
            payload={"file_id": file_id, "sub_format": sub_format},
            token=self._token,
        )
        link = payload.get("link")
        if not link:
            raise RuntimeError(f"OpenSubtitles download response missing link: {payload}")

        content = await asyncio.to_thread(self._request_bytes, link)
        filename = payload.get("file_name") or f"{file_id}.{sub_format}"
        return content, filename

    def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        params: Optional[dict] = None,
        payload: Optional[dict] = None,
        token: Optional[str] = None,
    ) -> dict:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        data = None
        headers = dict(self._headers)
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"

        req = urllib.request.Request(url, data=data, method=method)
        for key, value in headers.items():
            req.add_header(key, value)

        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenSubtitles HTTP {exc.code}: {body[:300]}") from exc
        except Exception as exc:
            raise RuntimeError(f"OpenSubtitles request failed: [{type(exc).__name__}] {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            snippet = raw[:300].replace("\n", " ")
            raise RuntimeError(f"OpenSubtitles returned non-JSON content: {snippet}") from exc

    def _request_bytes(self, url: str) -> bytes:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", self._headers["User-Agent"])
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    def _proxy_json(self, path: str, payload: dict) -> dict:
        if not self.proxy_url:
            raise RuntimeError("OpenSubtitles proxy URL is not configured")

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{self.proxy_url}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        req.add_header("X-Proxy-Key", self.proxy_key)

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenSubtitles proxy HTTP {exc.code}: {body[:300]}") from exc
        except Exception as exc:
            raise RuntimeError(f"OpenSubtitles proxy request failed: [{type(exc).__name__}] {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            snippet = raw[:300].replace("\n", " ")
            raise RuntimeError(f"OpenSubtitles proxy returned non-JSON content: {snippet}") from exc

    def _build_queries(self, title: str, year: Optional[int], original_name: str) -> list[str]:
        queries: list[str] = []
        release = self._release_hint(original_name)
        if release:
            queries.append(release)
        clean_title = self._normalize_query(title)
        if clean_title:
            if year:
                queries.append(f"{clean_title} {year}")
            queries.append(clean_title)

        deduped: list[str] = []
        seen: set[str] = set()
        for q in queries:
            key = q.lower().strip()
            if key and key not in seen:
                seen.add(key)
                deduped.append(q)
        return deduped

    def _extract_candidates(self, item: dict) -> list[dict]:
        attrs = item.get("attributes", {}) or {}
        files = attrs.get("files", []) or []
        release = attrs.get("release") or ""
        feature = attrs.get("feature_details", {}) or {}
        result: list[dict] = []
        for file_info in files:
            file_id = file_info.get("file_id")
            if not file_id:
                continue
            result.append(
                {
                    "file_id": int(file_id),
                    "release": release,
                    "file_name": file_info.get("file_name") or "",
                    "language": attrs.get("language") or "",
                    "hearing_impaired": bool(attrs.get("hearing_impaired")),
                    "download_count": int(attrs.get("download_count") or 0),
                    "ratings": float(attrs.get("ratings") or 0.0),
                    "from_trusted": bool(attrs.get("from_trusted")),
                    "year": feature.get("year"),
                    "feature_title": feature.get("title") or "",
                }
            )
        return result

    def _candidate_score(
        self,
        *,
        item: dict,
        release_hint: str,
        title_hint: str,
        year: Optional[int],
    ) -> tuple:
        haystack = " ".join(
            [
                str(item.get("release") or ""),
                str(item.get("file_name") or ""),
                str(item.get("feature_title") or ""),
            ]
        ).lower()
        exact_release = 0
        release_overlap = 0
        if release_hint:
            exact_release = int(release_hint.lower() in haystack)
            release_tokens = [t for t in re.split(r"[^a-z0-9]+", release_hint.lower()) if len(t) > 2]
            release_overlap = sum(1 for t in release_tokens if t in haystack)

        title_match = int(title_hint.lower() in haystack) if title_hint else 0
        year_match = int(bool(year) and str(year) in haystack)
        trusted = int(bool(item.get("from_trusted")))
        sdh_pref = int(bool(item.get("hearing_impaired")) == bool(self.prefer_sdh))
        return (
            exact_release,
            release_overlap,
            title_match,
            year_match,
            trusted,
            sdh_pref,
            int(item.get("download_count") or 0),
            float(item.get("ratings") or 0.0),
        )

    def _release_hint(self, original_name: str) -> str:
        name = (original_name or "").strip()
        if not name:
            return ""
        name = re.sub(r"\.[a-z0-9]{2,4}$", "", name, flags=re.IGNORECASE)
        return self._normalize_query(name)

    def _normalize_query(self, text: str) -> str:
        text = (text or "").replace("_", " ").replace(".", " ")
        text = re.sub(r"\[[^\]]+\]|\([^)]+\)", " ", text)
        text = re.sub(r"[^A-Za-z0-9+\- ]", " ", text)
        return re.sub(r"\s+", " ", text).strip()
