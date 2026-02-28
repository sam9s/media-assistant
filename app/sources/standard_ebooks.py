"""
Standard Ebooks search client — searches standardebooks.org.
No API key required. Returns EPUB downloads only (highest quality).
Uses the OPDS catalog endpoint for structured data.
"""
from typing import Optional
import re

import httpx

SEARCH_URL = "https://standardebooks.org/ebooks"
OPDS_SEARCH_URL = "https://standardebooks.org/feeds/opds/all"


async def search_standard_ebooks(query: str, limit: int = 5) -> list[dict]:
    """
    Search Standard Ebooks via their OPDS feed + title filter.
    Returns a list of result dicts with standardised fields.
    """
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(
                SEARCH_URL,
                params={"query": query},
                headers={"Accept": "application/atom+xml, application/xml, text/xml, */*"},
            )
            resp.raise_for_status()
            text = resp.text
    except Exception:
        return []

    return _parse_html_results(text, query, limit)


def _parse_html_results(html: str, query: str, limit: int) -> list[dict]:
    """
    Parse Standard Ebooks search results HTML.
    Extracts title, author, EPUB download URL, and cover.
    """
    results = []

    # Find all book blocks — SE uses <li> elements with class containing "book"
    book_blocks = re.findall(
        r'<li[^>]*class="[^"]*book[^"]*"[^>]*>(.*?)</li>',
        html,
        re.DOTALL,
    )

    for block in book_blocks:
        if len(results) >= limit:
            break
        title = _extract_tag(block, "title") or _extract_attr(block, "img", "alt", "")
        author = _extract_meta_author(block)
        epub_url = _extract_epub_link(block)
        cover_url = _extract_cover(block)

        if not epub_url or not title:
            continue

        results.append({
            "title": title.strip(),
            "author": author.strip() if author else "Unknown",
            "year": None,
            "format": "epub",
            "download_url": epub_url,
            "cover_url": cover_url,
            "source": "Standard Ebooks",
            "source_id": epub_url,
            "size_mb": None,
        })

    return results


def _extract_tag(html: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.DOTALL | re.IGNORECASE)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return None


def _extract_attr(html: str, tag: str, attr: str, default: str = "") -> str:
    m = re.search(rf'<{tag}[^>]*\s{attr}="([^"]*)"', html, re.IGNORECASE)
    return m.group(1) if m else default


def _extract_meta_author(block: str) -> Optional[str]:
    m = re.search(r'<p[^>]*class="[^"]*author[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL | re.IGNORECASE)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return None


def _extract_epub_link(block: str) -> Optional[str]:
    # Look for .epub download link
    m = re.search(r'href="(/ebooks/[^"]+\.epub)"', block)
    if m:
        return f"https://standardebooks.org{m.group(1)}"
    # Also try kepub or other epub variants
    m = re.search(r'href="(/ebooks/[^"]+/[^"]+)"', block)
    if m:
        path = m.group(1)
        if "epub" in path.lower():
            return f"https://standardebooks.org{path}"
    return None


def _extract_cover(block: str) -> Optional[str]:
    m = re.search(r'src="(/images/covers/[^"]+)"', block)
    if m:
        return f"https://standardebooks.org{m.group(1)}"
    return None
