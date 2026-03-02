"""
Archive.org search client — searches Internet Archive for ebooks.
Largest collection, good for obscure titles. No API key needed.
"""
import re
from typing import Optional

import httpx

ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
ARCHIVE_DOWNLOAD_URL = "https://archive.org/download"


async def search_archive_org(query: str, limit: int = 5) -> list[dict]:
    """
    Search Internet Archive for ebooks. Prefers EPUB over PDF.
    Returns a list of result dicts with standardised fields.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                ARCHIVE_SEARCH_URL,
                params={
                    "q": f"{query} AND mediatype:texts",
                    "fl[]": "identifier,title,creator,date,format",
                    "sort[]": "downloads desc",
                    "rows": limit * 3,  # over-fetch to filter for epub/pdf
                    "page": 1,
                    "output": "json",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return []

    docs = data.get("response", {}).get("docs", [])
    results = []

    for doc in docs:
        if len(results) >= limit:
            break

        identifier = doc.get("identifier", "")
        if not identifier:
            continue

        doc_formats = doc.get("format", [])
        if isinstance(doc_formats, str):
            doc_formats = [doc_formats]

        # Determine best available format
        fmt, file_ext = _pick_format(doc_formats)
        if not fmt:
            continue

        # Build download URL — Archive.org serves files at /download/{id}/{id}.{ext}
        download_url = f"{ARCHIVE_DOWNLOAD_URL}/{identifier}/{identifier}.{file_ext}"

        # Clean author name (Archive.org often gives "Last, First, YYYY-YYYY")
        raw_author = doc.get("creator", "Unknown")
        if isinstance(raw_author, list):
            raw_author = raw_author[0] if raw_author else "Unknown"
        author = _clean_author(str(raw_author))

        # Extract year from date field like "1897-01-01" or "1897"
        year = _extract_year(doc.get("date", ""))

        results.append({
            "title": doc.get("title", "Unknown"),
            "author": author,
            "year": year,
            "format": fmt,
            "download_url": download_url,
            "cover_url": f"https://archive.org/services/img/{identifier}",
            "source": "Archive.org",
            "source_id": identifier,
            "size_mb": None,
        })

    return results


def _pick_format(formats: list[str]) -> tuple[str, str]:
    """Return (format_name, file_extension) preferring EPUB > PDF > CBZ > CBR."""
    fmt_lower = [f.lower() for f in formats]
    if "epub" in fmt_lower:
        return "epub", "epub"
    if "pdf" in fmt_lower:
        return "pdf", "pdf"
    if "cbz" in fmt_lower:
        return "cbz", "cbz"
    if "cbr" in fmt_lower:
        return "cbr", "cbr"
    return "", ""


def _clean_author(name: str) -> str:
    """
    Clean author name from Archive.org format.
    Examples:
      'Carroll, Lewis, 1832-1898' -> 'Lewis Carroll'
      'Dumas, Alexandre, 1802-'   -> 'Alexandre Dumas'
      'Jules Verne'               -> 'Jules Verne'
    """
    # Strip trailing year range: ', YYYY-YYYY' or ', YYYY-' or just ', YYYY'
    name = re.sub(r",\s*\d{3,4}[-–]\d{0,4}\s*$", "", name).strip()
    name = re.sub(r",\s*\d{3,4}\s*$", "", name).strip()
    # Convert 'Last, First' to 'First Last'
    if "," in name:
        parts = name.split(",", 1)
        return f"{parts[1].strip()} {parts[0].strip()}"
    return name


def _extract_year(date_str: str) -> Optional[int]:
    """Extract year from '1897-01-01' or '1897' strings."""
    if not date_str:
        return None
    m = re.match(r"(\d{4})", str(date_str))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None
