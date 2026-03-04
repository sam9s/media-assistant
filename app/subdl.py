import urllib.error
import urllib.parse
import urllib.request


class SubDLClient:
    def __init__(self, api_key: str, languages: str = "EN"):
        self.api_key = api_key.strip()
        self.languages = (languages or "EN").strip()
        self.base_url = "https://api.subdl.com/api/v1/subtitles"
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    def search(
        self,
        *,
        title: str,
        year: int | None = None,
        original_name: str = "",
        media_type: str = "movie",
        languages: str | None = None,
        limit: int = 10,
    ) -> dict:
        if not self.api_key:
            raise RuntimeError("SUBDL_API_KEY is not configured")

        params = {
            "api_key": self.api_key,
            "type": media_type,
            "languages": (languages or self.languages or "EN").upper(),
            "subs_per_page": max(1, min(limit, 30)),
            "comment": 1,
            "releases": 1,
            "hi": 1,
        }

        release_hint = self._clean_text(original_name)
        if release_hint:
            params["file_name"] = release_hint
        else:
            params["film_name"] = self._clean_text(title)

        if year:
            params["year"] = year

        payload = self._request_json(params)
        subtitles = payload.get("subtitles", []) or []
        return {
            "status": payload.get("status", False),
            "results": payload.get("results", []) or [],
            "subtitles": [self._normalize_subtitle(item) for item in subtitles],
        }

    def _request_json(self, params: dict) -> dict:
        url = f"{self.base_url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", self.user_agent)

        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"SubDL HTTP {exc.code}: {body[:300]}") from exc
        except Exception as exc:
            raise RuntimeError(f"SubDL request failed: [{type(exc).__name__}] {exc}") from exc

        import json

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            snippet = raw[:300].replace("\n", " ")
            raise RuntimeError(f"SubDL returned non-JSON content: {snippet}") from exc

    def _normalize_subtitle(self, item: dict) -> dict:
        subtitle_id = item.get("subtitle_id")
        language = item.get("language") or item.get("lang") or ""
        releases = item.get("releases") or []
        release_name = ""
        if isinstance(releases, list) and releases:
            release_name = str(releases[0])
        elif isinstance(releases, str):
            release_name = releases

        comment = item.get("comment") or ""
        hearing_impaired = bool(item.get("hi"))
        download_url = None
        if item.get("url"):
            url = str(item.get("url"))
            if url.startswith("http://") or url.startswith("https://"):
                download_url = url
            else:
                download_url = f"https://dl.subdl.com{url}"
        elif item.get("subdl_link_id"):
            download_url = f"https://dl.subdl.com/subtitle/{item['subdl_link_id']}.zip"

        return {
            "subtitle_id": subtitle_id,
            "language": language,
            "release_name": release_name,
            "hearing_impaired": hearing_impaired,
            "comment": comment,
            "download_url": download_url,
        }

    def download_archive(self, download_url: str) -> bytes:
        if not download_url:
            raise RuntimeError("SubDL download URL is missing")

        req = urllib.request.Request(download_url, method="GET")
        req.add_header("User-Agent", self.user_agent)

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"SubDL download HTTP {exc.code}: {body[:300]}") from exc
        except Exception as exc:
            raise RuntimeError(f"SubDL download failed: [{type(exc).__name__}] {exc}") from exc

    def _clean_text(self, text: str) -> str:
        import re

        text = (text or "").replace(".", " ").replace("_", " ").strip()
        text = re.sub(r"\s+", " ", text)
        return text
