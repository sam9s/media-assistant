"""
Gutendex search client — queries the Gutenberg REST API (gutendex.com).
Free, no API key required. Returns up to `limit` EPUB-preferred results.
"""
from typing import Optional

import httpx

GUTENDEX_URL = "https://gutendex.com/books"


async def search_gutendex(query: str, limit: int = 5) -> list[dict]:
    """
    Search Project Gutenberg via the Gutendex API.
    Returns a list of result dicts with standardised fields.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                GUTENDEX_URL,
                params={"search": query, "languages": "en"},
            )
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
            or formats.get("text/html")
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
            "cover_url": formats.get("image/jpeg", None),
            "source": "Gutenberg",
            "source_id": str(book.get("id", "")),
            "size_mb": None,  # Gutendex doesn't provide file sizes
        })

    return results


def _detect_format(formats: dict) -> Optional[str]:
    if "application/epub+zip" in formats or "application/epub" in formats:
        return "epub"
    if "application/pdf" in formats:
        return "pdf"
    return None


def _format_author(name: str) -> str:
    """Convert 'Last, First' to 'First Last'."""
    if "," in name:
        parts = name.split(",", 1)
        return f"{parts[1].strip()} {parts[0].strip()}"
    return name


def _extract_year(book: dict) -> Optional[int]:
    """Try to extract publication year from subjects or copyright."""
    copyright_field = book.get("copyright")
    if isinstance(copyright_field, int):
        return copyright_field
    # Fall back to None — year is often not reliable in Gutenberg data
    return None
