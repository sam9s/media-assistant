#!/usr/bin/env python
"""Copy a local FLAC file/folder to VPS staging and trigger /music/import."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def api_call(api_url: str, api_key: str, payload: dict) -> dict:
    resp = requests.post(
        api_url.rstrip("/") + "/music/import",
        headers={"X-API-Key": api_key},
        json=payload,
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code} {resp.text}")
    return resp.json()


def get_status(api_url: str, api_key: str, download_id: str) -> dict:
    resp = requests.get(
        api_url.rstrip("/") + f"/music/status/{download_id}",
        headers={"X-API-Key": api_key},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code} {resp.text}")
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_path", help="Local FLAC file or album folder to import")
    parser.add_argument("--language", required=True, choices=["english", "hindi", "punjabi"])
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--ssh-host", required=True, help="Example: root@69.62.73.167")
    parser.add_argument("--remote-staging-root", default="/mnt/cloud/gdrive/Media/Music/Downloads/manual_imports")
    parser.add_argument("--mode", default="auto", choices=["auto", "album", "track"])
    parser.add_argument("--wait", action="store_true", help="Poll status until done/failed")
    args = parser.parse_args()

    source = Path(args.source_path).expanduser()
    if not source.exists():
        raise SystemExit(f"Source path not found: {source}")

    token = uuid.uuid4().hex[:12]
    remote_base = f"{args.remote_staging_root}/{token}"
    remote_target = f"{remote_base}/{source.name}"

    run(["ssh", args.ssh_host, f"mkdir -p {remote_base}"])
    if source.is_dir():
        run(["scp", "-r", str(source), f"{args.ssh_host}:{remote_base}/"])
    else:
        run(["scp", str(source), f"{args.ssh_host}:{remote_base}/"])

    result = api_call(
        args.api_url,
        args.api_key,
        {
            "source_path": remote_target,
            "language": args.language,
            "mode": args.mode,
        },
    )

    output = {
        "staged_to": remote_target,
        "import": result,
    }

    if not args.wait:
        print(json.dumps(output, indent=2))
        return 0

    download_id = result["download_id"]
    while True:
        status = get_status(args.api_url, args.api_key, download_id)
        output["status"] = status
        if status.get("status") in {"done", "failed"}:
            break
        time.sleep(5)

    print(json.dumps(output, indent=2))
    return 0 if output["status"].get("status") == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
