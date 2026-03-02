"""
Gutendex search client — queries the Gutenberg REST API (gutendex.com).
Free, no API key required. Returns up to `limit` EPUB-preferred results.
"""
import re
from typing import Optional

import httpx

GUTENDEX_URL = "https://gutendex.com/books"


async def search_gutendex(query: str, limit: int = 5) -> list[dict]:
    """
    Search Project Gutenberg via the Gutendex API.
    Returns a list of result dicts with standardised fields.
    NOTE: No language filter — classic works often lack language tags.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(GUTENDEX_URL, params={"search": query})
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    results = []
    for book in data.get("results", [])[:limit]:
        formats = book.get("formats", {})

        # Prefer EPUB, then PDF
        download_url = (
            formats.get("application/epub+zip")
            or formats.get("application/epub")
            or formats.get("application/pdf")
        )
        if not download_url:
            continue

        fmt = _detect_format(formats)
        if not fmt:
            continue

        authors = book.get("authors", [])
        author = _format_author(authors[0].get("name", "Unknown")) if authors else "Unknown"

        results.append({
            "title": book.get("title", "Unknown"),
            "author": author,
            "year": _extract_year(book),
            "format": fmt,
            "download_url": download_url,
            "cover_url": formats.get("image/jpeg"),
            "source": "Gutenberg",
            "source_id": str(book.get("id", "")),
            "size_mb": None,
        })

    return results


def _detect_format(formats: dict) -> Optional[str]:
    if "application/epub+zip" in formats or "application/epub" in formats:
        return "epub"
    if "application/pdf" in formats:
        return "pdf"
    return None


def _format_author(name: str) -> str:
    """
    Convert Gutenberg 'Last, First, YYYY-YYYY' to 'First Last'.
    Examples:
      'Dumas, Alexandre'          -> 'Alexandre Dumas'
      'Dumas, Alexandre, 1802-'   -> 'Alexandre Dumas'
      'Carroll, Lewis, 1832-1898' -> 'Lewis Carroll'
    """
    # Strip trailing year range like ', 1802-1870' or ', 1832-' or ', 1832-1898'
    name = re.sub(r",\s*\d{4}-\d{0,4}\s*$", "", name).strip()
    if "," in name:
        parts = name.split(",", 1)
        return f"{parts[1].strip()} {parts[0].strip()}"
    return name


def _extract_year(book: dict) -> Optional[int]:
    copyright_field = book.get("copyright")
    if isinstance(copyright_field, int):
        return copyright_field
    return None
