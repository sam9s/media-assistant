"""
Standard Ebooks search client — scrapes standardebooks.org search results.
Best source: professional EPUB formatting, DRM-free, public domain.
HTML structure: articles with typeof="schema:Book" about="/ebooks/author/title[/translator]"
EPUB download URL pattern: https://standardebooks.org{about}/downloads/{slug}.epub
"""
import re
from typing import Optional

import httpx

SE_BASE = "https://standardebooks.org"
SE_SEARCH_URL = f"{SE_BASE}/ebooks"


async def search_standard_ebooks(query: str, limit: int = 5) -> list[dict]:
    """
    Search Standard Ebooks. Parses the HTML search results page.
    Returns a list of result dicts with standardised fields.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                SE_SEARCH_URL,
                params={"query": query},
                headers={"User-Agent": "Mozilla/5.0 (compatible; SamAssist/2.0)"},
                follow_redirects=True,
            )
            resp.raise_for_status()
            html = resp.text
    except Exception:
        return []

    # Find all book entries: typeof="schema:Book" about="/ebooks/..."
    # Pattern: about="/ebooks/author-name/book-title[/translator]"
    book_paths = re.findall(
        r'typeof="schema:Book"[^>]+about="(/ebooks/[^"]+)"',
        html,
    )
    # Also catch the reverse attribute order
    book_paths += re.findall(
        r'about="(/ebooks/[^"]+)"[^>]+typeof="schema:Book"',
        html,
    )
    # Deduplicate preserving order
    seen: set = set()
    unique_paths: list = []
    for p in book_paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    results = []
    for path in unique_paths:
        if len(results) >= limit:
            break

        title, author = _parse_se_path(path)
        if not title:
            continue

        # SE EPUB slug = path segments joined with underscores (strip leading /ebooks/)
        slug = path.lstrip("/").replace("ebooks/", "", 1).replace("/", "_")
        epub_url = f"{SE_BASE}{path}/downloads/{slug}.epub"

        results.append({
            "title": title,
            "author": author,
            "year": None,  # Standard Ebooks doesn't include year in search results
            "format": "epub",
            "download_url": epub_url,
            "cover_url": f"{SE_BASE}{path}/downloads/{slug}_cover.jpg",
            "source": "Standard Ebooks",
            "source_id": path,
            "size_mb": None,
        })

    return results


def _parse_se_path(path: str) -> tuple[str, str]:
    """
    Convert /ebooks/alexandre-dumas/the-count-of-monte-cristo/chapman-and-hall
    to title='The Count of Monte Cristo', author='Alexandre Dumas'.
    """
    # Strip leading /ebooks/
    parts = path.strip("/").split("/")
    # parts[0] = author slug, parts[1] = title slug, parts[2+] = translator (optional)
    if len(parts) < 2:
        return "", ""

    author = _slug_to_title(parts[1 if parts[0] == "ebooks" else 0])
    title = _slug_to_title(parts[2 if parts[0] == "ebooks" else 1])

    # Reorder: remove 'ebooks' prefix if present
    actual_parts = parts[1:] if parts[0] == "ebooks" else parts
    if len(actual_parts) >= 2:
        author = _slug_to_title(actual_parts[0])
        title = _slug_to_title(actual_parts[1])
    return title, author


def _slug_to_title(slug: str) -> str:
    """Convert 'the-count-of-monte-cristo' -> 'The Count Of Monte Cristo'."""
    return slug.replace("-", " ").title()
