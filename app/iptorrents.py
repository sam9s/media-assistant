import re
import xml.etree.ElementTree as ET
from typing import Any, Optional
from urllib.parse import quote_plus

import httpx


def _parse_size(text: str) -> str:
    """Extract file size from description text. Handles GB/MB/TB."""
    match = re.search(r"([\d.]+\s*(?:GB|MB|TB))", text, re.IGNORECASE)
    return match.group(1).strip() if match else "unknown"


def _parse_seeders(text: str) -> Optional[int]:
    """Extract seeder count from description. Returns None if not found (iptorrents RSS omits this)."""
    match = re.search(r"[Ss]eed(?:ers?)?\s*:?\s*(\d+)", text)
    return int(match.group(1)) if match else None


async def search_iptorrents(
    rss_base_url: str,
    query: str,
    quality: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    Search iptorrents RSS using server-side q= parameter.
    The rss_base_url should be the full URL without a q= param, e.g.:
      https://iptorrents.com/t.rss?u=...;tp=...;48;20;101;s0=10
    We append ;q={query} to get server-filtered results.
    """
    # iptorrents uses semicolons as separators — append q= with + for spaces
    encoded_query = query.replace(" ", "+")
    url = f"{rss_base_url};q={encoded_query}"

    transport = httpx.AsyncHTTPTransport(retries=3)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30, transport=transport) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        xml_content = resp.text

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    results: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""

        if not title:
            continue

        # Optional quality filter (client-side, since server already filtered by query)
        if quality and quality.lower() not in title.lower():
            continue

        # iptorrents puts the .torrent download URL in <link>, NOT <enclosure>
        # URL format: https://iptorrents.com/download.php/{id}/{name}.torrent?torrent_pass={key}
        link_el = item.find("link")
        torrent_url = link_el.text.strip() if link_el is not None and link_el.text else ""

        # Also check <enclosure> as fallback (some RSS variants use it)
        if not torrent_url:
            enclosure_el = item.find("enclosure")
            torrent_url = enclosure_el.get("url", "") if enclosure_el is not None else ""

        pub_date_el = item.find("pubDate")
        pub_date = pub_date_el.text.strip() if pub_date_el is not None and pub_date_el.text else ""

        # Description may contain size/seeder info (not always present on iptorrents)
        desc_el = item.find("description")
        desc = desc_el.text or "" if desc_el is not None else ""

        # Try to extract size from description, fall back to scanning the title
        size = _parse_size(desc) if desc else _parse_size(title)
        seeders = _parse_seeders(desc)

        results.append({
            "title": title,
            "size": size,
            "seeders": seeders,
            "torrent_url": torrent_url,
            "info_hash": "",
            "pub_date": pub_date,
            "source": "iptorrents",
        })

    # Results arrive sorted by seeders server-side (s0=10 in URL),
    # but re-sort locally in case seeder counts were parsed from descriptions
    results.sort(key=lambda x: x["seeders"] or 0, reverse=True)

    output: list[dict[str, Any]] = []
    for item_dict in results:
        if len(output) >= limit:
            break
        item_dict["index"] = len(output) + 1
        output.append(item_dict)

    return output
