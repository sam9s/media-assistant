from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    api_url = os.getenv("MEDIA_API_URL", "http://127.0.0.1:8765").rstrip("/")
    api_key = os.getenv("MEDIA_API_KEY")
    if not api_key:
        print("MEDIA_API_KEY is required", file=sys.stderr)
        return 2

    payload = {
        "sources": ["jellyfin", "navidrome", "kavita"],
        "window_days": int(os.getenv("RECOMMENDATIONS_INGEST_WINDOW_DAYS", "30")),
        "rebuild_signals": True,
    }
    with httpx.Client(timeout=300) as client:
        resp = client.post(
            f"{api_url}/recommendations/ingest/run",
            headers={"X-API-Key": api_key},
            json=payload,
        )
        resp.raise_for_status()
        print(resp.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
