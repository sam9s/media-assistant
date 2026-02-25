import xml.etree.ElementTree as ET
from typing import Optional

import httpx

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"


def _bytes_to_human(size_str: str) -> str:
    """Convert a byte count string (e.g. '12345678') to human-readable GB/MB."""
    try:
        b = int(size_str)
    except (ValueError, TypeError):
        return "unknown"
    if b >= 1_000_000_000:
        return f"{b / 1_000_000_000:.1f} GB"
    if b >= 1_000_000:
        return f"{b / 1_000_000:.0f} MB"
    return f"{b} B"


def _torznab_attrs(item: ET.Element) -> dict:
    """Extract all <torznab:attr name=... value=...> elements into a dict."""
    attrs = {}
    for el in item.findall(f"{{{TORZNAB_NS}}}attr"):
        name = el.get("name")
        value = el.get("value")
        if name:
            attrs[name] = value
    return attrs


async def search_jackett(
    base_url: str,
    api_key: str,
    query: str,
    quality: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    Search all configured Jackett indexers via Torznab API.

    base_url: e.g. "http://jackett:9117"
    api_key:  Jackett's API key (shown on its dashboard)
    query:    movie/show name
    quality:  optional quality filter applied client-side (e.g. "1080p")
    limit:    max results to return

    Each result has: title, size, seeders, torrent_url, source (tracker name), index.
    """
    if not api_key:
        # Jackett not configured yet — return empty list gracefully
        return []

    url = f"{base_url}/api/v2.0/indexers/all/results/torznab/api"
    params = {
        "apikey": api_key,
        "t": "search",
        "q": query,
        "limit": min(limit * 4, 100),  # over-fetch so quality filter has room
    }

    transport = httpx.AsyncHTTPTransport(retries=3)
    async with httpx.AsyncClient(timeout=30, transport=transport) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        xml_content = resp.text

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    results = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        if not title:
            continue

        # Optional quality filter
        if quality and quality.lower() not in title.lower():
            continue

        # Size (bytes → human)
        size_el = item.find("size")
        size = _bytes_to_human(size_el.text) if size_el is not None and size_el.text else "unknown"

        # Torrent download URL
        enclosure_el = item.find("enclosure")
        torrent_url = enclosure_el.get("url", "") if enclosure_el is not None else ""
        if not torrent_url:
            link_el = item.find("link")
            torrent_url = link_el.text.strip() if link_el is not None and link_el.text else ""

        # Torznab attributes (seeders, etc.)
        attrs = _torznab_attrs(item)
        seeders = int(attrs.get("seeders", 0) or 0)

        # Tracker name: Jackett puts it in <jackettindexer>TrackerName</jackettindexer>
        ji_el = item.find("jackettindexer")
        tracker = (ji_el.text.strip() if ji_el is not None and ji_el.text else None) \
                  or attrs.get("tracker") or attrs.get("indexer") or "jackett"

        pub_date_el = item.find("pubDate")
        pub_date = pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else ""

        results.append({
            "title": title,
            "size": size,
            "seeders": seeders,
            "torrent_url": torrent_url,
            "pub_date": pub_date,
            "source": tracker,
        })

    # Sort by seeders descending
    results.sort(key=lambda x: x["seeders"], reverse=True)

    top = results[:limit]
    for i, r in enumerate(top, start=1):
        r["index"] = i

    return top
