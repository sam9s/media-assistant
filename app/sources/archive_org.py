"""
Archive.org search client — searches the Internet Archive for ebooks.
No API key required. Prefers EPUB, falls back to PDF.
Used as the third source / fallback for obscure titles.
"""
from typing import Optional
import re

import httpx

SEARCH_URL = "https://archive.org/advancedsearch.php"
DOWNLOAD_BASE = "https://archive.org/download"


async def search_archive_org(query: str, limit: int = 5) -> list[dict]:
    """
    Search Internet Archive for ebooks.
    Returns a list of result dicts with standardised fields.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                SEARCH_URL,
                params={
                    "q": f"{query} AND mediatype:texts AND (format:EPUB OR format:PDF)",
                    "fl[]": "identifier,title,creator,year,format",
                    "rows": limit * 2,  # fetch extra to filter non-EPUB/PDF
                    "page": 1,
                    "output": "json",
                    "sort[]": "downloads desc",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    docs = data.get("response", {}).get("docs", [])
    results = []

    for doc in docs:
        identifier = doc.get("identifier", "")
        if not identifier:
            continue

        fmt, download_url = await _resolve_download_url(identifier)
        if not download_url:
            continue

        title = doc.get("title", "Unknown")
        creator = doc.get("creator", "Unknown")
        if isinstance(creator, list):
            creator = creator[0] if creator else "Unknown"

        year_raw = doc.get("year")
        year: Optional[int] = None
        if year_raw:
            try:
                year = int(str(year_raw)[:4])
            except (ValueError, TypeError):
                pass

        results.append({
            "title": title.strip() if title else "Unknown",
            "author": str(creator).strip() if creator else "Unknown",
            "year": year,
            "format": fmt,
            "download_url": download_url,
            "cover_url": f"https://archive.org/services/img/{identifier}",
            "source": "Archive.org",
            "source_id": identifier,
            "size_mb": None,
        })

        if len(results) >= limit:
            break

    return results


async def _resolve_download_url(identifier: str) -> tuple[str, Optional[str]]:
    """
    Fetch the file list for an Archive.org item and return the best
    (EPUB preferred, PDF fallback) download URL.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://archive.org/metadata/{identifier}/files",
                timeout=8,
            )
            resp.raise_for_status()
            files = resp.json().get("result", [])
    except Exception:
        return "unknown", None

    epub_url = None
    pdf_url = None

    for f in files:
        name = f.get("name", "")
        fmt = f.get("format", "").lower()
        if not epub_url and ("epub" in fmt or name.lower().endswith(".epub")):
            epub_url = f"{DOWNLOAD_BASE}/{identifier}/{name}"
        if not pdf_url and ("pdf" in fmt or name.lower().endswith(".pdf")):
            pdf_url = f"{DOWNLOAD_BASE}/{identifier}/{name}"

    if epub_url:
        return "epub", epub_url
    if pdf_url:
        return "pdf", pdf_url
    return "unknown", None
