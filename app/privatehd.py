import xml.etree.ElementTree as ET
from typing import Optional

import httpx

# Jackett Torznab uses this namespace for seeders/size attributes
_TORZNAB = "http://torznab.com/schemas/2015/feed"


def _bytes_to_str(size_bytes: int) -> str:
    """Convert bytes to human-readable size string."""
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / 1024**3:.1f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / 1024**2:.0f} MB"
    return f"{size_bytes / 1024:.0f} KB"


def _torznab_attr(item: ET.Element, name: str) -> Optional[str]:
    """Extract a torznab:attr value by name from an RSS <item>."""
    for el in item.findall(f"{{{_TORZNAB}}}attr"):
        if el.get("name") == name:
            return el.get("value")
    return None


async def search_privatehd(
    jackett_url: str,
    jackett_api_key: str,
    query: str,
    quality: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    Search PrivateHD via Jackett's dedicated privatehd indexer (Torznab API).

    Same approach as iptorrents RSS: build a URL with the query appended,
    fetch XML, parse results.

    URL pattern (same as what works in the browser):
      {jackett_url}/api/v2.0/indexers/privatehd/results/torznab/api
        ?apikey={jackett_api_key}&t=search&cat=&q={query}
    """
    url = (
        f"{jackett_url}/api/v2.0/indexers/privatehd/results/torznab/api"
        f"?apikey={jackett_api_key}&t=search&cat=&q={query.replace(' ', '+')}"
    )

    transport = httpx.AsyncHTTPTransport(retries=3)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30, transport=transport) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        xml_content = resp.text

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return []

    # Jackett returns <error code="..." description="..."/> on failure
    if root.tag == "error":
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    quality_filter = quality.lower() if quality else ""

    results = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue

        if quality_filter and quality_filter not in title.lower():
            continue

        # Torrent download URL from <enclosure>
        torrent_url = ""
        enclosure_el = item.find("enclosure")
        if enclosure_el is not None:
            torrent_url = enclosure_el.get("url", "")

        # Size from torznab:attr or enclosure length
        size_str = "unknown"
        size_attr = _torznab_attr(item, "size")
        if size_attr and size_attr.isdigit():
            size_str = _bytes_to_str(int(size_attr))
        elif enclosure_el is not None:
            length = enclosure_el.get("length", "")
            if length.isdigit():
                size_str = _bytes_to_str(int(length))

        # Seeders from torznab:attr
        seeders: Optional[int] = None
        seeders_attr = _torznab_attr(item, "seeders")
        if seeders_attr is not None and seeders_attr.isdigit():
            seeders = int(seeders_attr)

        pub_date_el = item.find("pubDate")
        pub_date = (pub_date_el.text or "").strip() if pub_date_el is not None else ""

        results.append({
            "title": title,
            "size": size_str,
            "seeders": seeders,
            "torrent_url": torrent_url,
            "pub_date": pub_date,
            "source": "PrivateHD",
        })

        if len(results) >= limit:
            break

    for i, r in enumerate(results, start=1):
        r["index"] = i

    return results
