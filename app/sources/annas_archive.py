"""
Anna's Archive search client — scrapes annas-archive.gl (and mirror fallbacks).

SEARCH: Works fully — returns results with source="AnnasArchive" and
        source_id="/md5/{hash}" instead of a direct download_url.

RESOLVER: Structured for future activation. Currently all Anna's Archive
          download endpoints are protected by DDoS-Guard (requires JS execution)
          or partner sites that block server IPs. If ANNA_ARCHIVE_COOKIE is set
          in .env, it will be used as the DDoS-Guard bypass session cookie.

HTML structure confirmed via live VPS testing (2026-03-03):
  - Search result blocks: div.flex.pt-3.pb-3.border-b
  - MD5 path: href="/md5/{hash}" on the cover <a>
  - Title/author: data-content attrs in the fallback cover div
  - Metadata (format, size, lang): div.text-gray-800.font-semibold.text-sm
  - Download links on detail page: a.js-download-link with href=/slow_download/... or /fast_download/...
"""
import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("uvicorn.error")

MIRRORS = [
    "https://annas-archive.gl",
    "https://annas-archive.li",
    "https://annas-archive.se",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Module-level cache: remember the last mirror that worked
_active_mirror: Optional[str] = None


def _is_valid_search_html(html: str) -> bool:
    """Return True if the HTML looks like a real search results page (has /md5/ links)."""
    return "/md5/" in html and "annas-archive" in html.lower()


def _is_ddos_guard(html: str) -> bool:
    """Return True if the response is a DDoS-Guard challenge page."""
    return "ddos-guard" in html.lower() or "checking your browser" in html.lower()


async def _find_working_mirror(query: str) -> tuple[Optional[str], Optional[str]]:
    """
    Try each mirror in order. Return (mirror_url, search_html) for the first
    mirror that returns a valid search results page.
    """
    global _active_mirror
    mirrors_to_try = [_active_mirror] + [m for m in MIRRORS if m != _active_mirror]
    mirrors_to_try = [m for m in mirrors_to_try if m]  # remove None

    async with httpx.AsyncClient(timeout=20, headers=_HEADERS, follow_redirects=True) as client:
        for mirror in mirrors_to_try:
            try:
                resp = await client.get(
                    f"{mirror}/search",
                    params={"q": query, "ext": "epub"},
                )
                if resp.status_code == 200:
                    html = resp.text
                    if _is_valid_search_html(html):
                        _active_mirror = mirror
                        return mirror, html
            except Exception as exc:
                logger.debug("AA mirror %s failed: %s", mirror, exc)

    return None, None


def _parse_metadata_block(text: str) -> dict:
    """
    Parse the metadata string like:
      "English [en] · EPUB · 1.4MB · 2019 · 📘 Book (non-fiction) · ..."
    Returns dict with format, size_mb, year keys.
    """
    parts = [p.strip() for p in text.split("·")]
    fmt = None
    size_mb = None
    year = None

    for part in parts:
        part_clean = part.strip()
        # Format: "EPUB", "PDF", "MOBI", "AZW3", "CBZ", etc.
        if re.match(r'^(epub|pdf|mobi|azw3|cbz|cbr|fb2|djvu)$', part_clean, re.IGNORECASE):
            fmt = part_clean.lower()
        # Size: "1.4MB", "850KB", "1.1 MB"
        elif re.match(r'^[\d.]+\s*(MB|KB|GB)$', part_clean, re.IGNORECASE):
            m = re.match(r'^([\d.]+)\s*(MB|KB|GB)$', part_clean, re.IGNORECASE)
            if m:
                num, unit = float(m.group(1)), m.group(2).upper()
                if unit == "KB":
                    size_mb = round(num / 1024, 3)
                elif unit == "GB":
                    size_mb = round(num * 1024, 1)
                else:
                    size_mb = round(num, 2)
        # Year: 4-digit number between 1000–2099
        elif re.match(r'^(1[0-9]{3}|20[0-9]{2})$', part_clean):
            year = int(part_clean)

    return {"format": fmt, "size_mb": size_mb, "year": year}


def _parse_search_results(html: str, limit: int) -> list[dict]:
    """
    Parse Anna's Archive search results HTML into a list of normalised result dicts.
    Deduplicates by title, preferring EPUB over PDF within AA results.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    seen_titles: set[str] = set()

    # Find all result blocks: div elements with classes "flex pt-3 pb-3 border-b"
    blocks = soup.find_all("div", class_=lambda c: c and "flex" in c and "pt-3" in c and "pb-3" in c and "border-b" in c)

    for block in blocks:
        if len(results) >= limit:
            break

        # Extract MD5 path from cover <a>
        cover_a = block.find("a", href=lambda h: h and h.startswith("/md5/"))
        if not cover_a:
            continue
        source_id = cover_a["href"]  # e.g. "/md5/890a91b4f0cf047b5276c9f7d522adc6"

        # Extract title and author from data-content attributes (most reliable)
        title = None
        author = None
        fallback_divs = block.find_all("div", attrs={"data-content": True})
        for div in fallback_divs:
            cls = " ".join(div.get("class", []))
            if "violet" in cls and not title:
                title = div.get("data-content", "").strip()
            elif "amber" in cls and not author:
                author = div.get("data-content", "").strip()

        # Fallback: extract title from the bold anchor link text
        if not title:
            title_a = block.find("a", href=lambda h: h and h.startswith("/md5/"),
                                  class_=lambda c: c and "font-semibold" in c)
            if title_a:
                title = title_a.get_text(strip=True)

        if not title:
            continue

        # Normalise: strip trailing commas, clean author list separators
        title = title.strip().rstrip(",")
        if author:
            # Anna's Archive may list "Author1, Author2" — keep first only for folder naming
            author = author.split(",")[0].strip()

        # Extract format/size/year from the metadata block
        meta_div = block.find("div", class_=lambda c: c and "text-gray-800" in c and "font-semibold" in c)
        meta = {}
        if meta_div:
            meta = _parse_metadata_block(meta_div.get_text())

        fmt = meta.get("format")
        if not fmt:
            continue  # skip results with no recognisable format

        # Deduplicate: skip if we already have a result with this title
        title_key = title.lower().strip()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        results.append({
            "title": title,
            "author": author or "Unknown",
            "year": meta.get("year"),
            "format": fmt,
            "download_url": None,       # No direct URL — resolver needed
            "source_id": source_id,     # "/md5/{hash}"
            "cover_url": None,
            "source": "AnnasArchive",
            "size_mb": meta.get("size_mb"),
        })

    return results


async def search_annas_archive(query: str, limit: int = 5) -> list[dict]:
    """
    Search Anna's Archive for ebooks. Returns up to `limit` results.
    Results include source_id="/md5/..." and NO download_url.
    The caller must use resolve_annas_download() to get a direct file URL.

    Returns [] on any failure (matches existing source pattern).
    """
    try:
        mirror, html = await _find_working_mirror(query)
        if not mirror or not html:
            logger.warning("Anna's Archive: no working mirror found for query %r", query)
            return []
        return _parse_search_results(html, limit)
    except Exception as exc:
        logger.warning("Anna's Archive search failed: %s", exc)
        return []


async def resolve_annas_download(source_id: str, cookie: Optional[str] = None) -> str:
    """
    Attempt to resolve an Anna's Archive source_id ("/md5/{hash}") to a direct
    download URL.

    Current status (2026-03-03):
      - /slow_download/ and /fast_download/ are protected by DDoS-Guard (requires JS)
      - Libgen partner sites block server-side requests from data-centre IPs
      - If ANNA_ARCHIVE_COOKIE is set in .env, it is used as the DDoS-Guard
        bypass session cookie, which may allow slow_download to work

    Raises:
        RuntimeError: always, until a working download path is confirmed.
                      Message describes exactly why the download failed.
    """
    mirror = _active_mirror or MIRRORS[0]
    md5 = source_id.lstrip("/md5/").strip("/")

    # Attempt slow_download[0] — if a session cookie is available, it might work
    download_url = f"{mirror}/slow_download/{md5}/0/0"
    headers = dict(_HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    try:
        async with httpx.AsyncClient(timeout=60, headers=headers, follow_redirects=True) as client:
            resp = await client.get(download_url)

        if _is_ddos_guard(resp.text):
            raise RuntimeError(
                "Anna's Archive slow_download is protected by DDoS-Guard. "
                "A browser session cookie (ANNA_ARCHIVE_COOKIE in .env) is required "
                "to bypass this. Download manually from: "
                f"{mirror}/md5/{md5}"
            )

        ct = resp.headers.get("content-type", "")
        if resp.status_code == 200 and any(t in ct for t in ("epub", "pdf", "octet-stream", "application/")):
            # Actual file returned — return the final URL for the downloader to use
            return str(resp.url)

        raise RuntimeError(
            f"Anna's Archive returned unexpected response (HTTP {resp.status_code}, "
            f"Content-Type: {ct}). "
            f"Download manually from: {mirror}/md5/{md5}"
        )

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Anna's Archive download request failed: [{type(exc).__name__}] {exc}. "
            f"Download manually from: {mirror}/md5/{md5}"
        ) from exc
