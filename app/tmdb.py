import re
from typing import Optional

import httpx

TMDB_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


class TMDBClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def get_metadata(self, query: str) -> Optional[dict]:
        """
        Search TMDB for a movie or TV show and return enriched metadata:
        title, year, rating, poster URL, IMDb link, overview.

        Makes two calls:
          1. /search/multi  → find best match, get media_type + tmdb_id
          2. /movie/{id} or /tv/{id} → get imdb_id + full details
        """
        # Strip trailing year from query — TMDB /search/multi doesn't handle "Title 2025" well
        tmdb_query = re.sub(r"\s+\d{4}$", "", query).strip() or query

        transport = httpx.AsyncHTTPTransport(retries=3)
        async with httpx.AsyncClient(timeout=15, transport=transport) as client:
            # --- Step 1: Multi search (handles both movies and TV shows) ---
            search_resp = await client.get(
                f"{TMDB_BASE}/search/multi",
                params={"query": tmdb_query, "api_key": self.api_key, "page": 1},
            )
            if search_resp.status_code != 200:
                return None

            hits = search_resp.json().get("results", [])
            # Keep only movies and TV shows (exclude person results)
            hits = [h for h in hits if h.get("media_type") in ("movie", "tv")]
            if not hits:
                return None

            best = hits[0]
            media_type = best.get("media_type", "movie")
            tmdb_id = best.get("id")

            # --- Step 2: Get full details (includes imdb_id) ---
            endpoint = "movie" if media_type == "movie" else "tv"
            detail_resp = await client.get(
                f"{TMDB_BASE}/{endpoint}/{tmdb_id}",
                params={"api_key": self.api_key},
            )

        detail = detail_resp.json() if detail_resp.status_code == 200 else {}

        # Title
        title = (
            best.get("title")
            or best.get("name")
            or detail.get("title")
            or detail.get("name", "Unknown")
        )

        # Year (from release_date or first_air_date)
        year_str = (
            best.get("release_date")
            or best.get("first_air_date")
            or detail.get("release_date")
            or detail.get("first_air_date")
            or ""
        )
        year = int(year_str[:4]) if len(year_str) >= 4 else None

        # Rating
        rating = round(
            float(best.get("vote_average") or detail.get("vote_average") or 0), 1
        )

        # Poster (prefer detail response, fall back to search result)
        poster_path = detail.get("poster_path") or best.get("poster_path") or ""
        poster_url = f"{TMDB_IMAGE_BASE}{poster_path}" if poster_path else None

        # IMDb (only available in detail response)
        imdb_id = detail.get("imdb_id")
        imdb_url = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else None

        # Overview (truncate to keep Telegram messages readable)
        overview = (best.get("overview") or detail.get("overview") or "").strip()
        if len(overview) > 280:
            overview = overview[:277] + "..."

        return {
            "title": title,
            "year": year,
            "rating": rating,
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "tmdb_url": f"https://www.themoviedb.org/{endpoint}/{tmdb_id}",
            "imdb_id": imdb_id,
            "imdb_url": imdb_url,
            "poster_url": poster_url,
            "overview": overview,
        }
