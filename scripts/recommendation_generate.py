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
        "mode": os.getenv("RECOMMENDATIONS_GENERATE_MODE", "weekly"),
        "lookback_days": int(os.getenv("RECOMMENDATIONS_DEFAULT_LOOKBACK_DAYS", "7")),
        "library_only": os.getenv("RECOMMENDATIONS_LIBRARY_ONLY", "false").lower() == "true",
        "include_new_finds": os.getenv("RECOMMENDATIONS_INCLUDE_NEW_FINDS", "true").lower() == "true",
        "limit_per_type": int(os.getenv("RECOMMENDATIONS_LIMIT_PER_TYPE", "1")),
    }
    with httpx.Client(timeout=300) as client:
        resp = client.post(
            f"{api_url}/recommendations/generate",
            headers={"X-API-Key": api_key},
            json=payload,
        )
        resp.raise_for_status()
        print(resp.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
