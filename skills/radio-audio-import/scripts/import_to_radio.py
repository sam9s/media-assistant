#!/usr/bin/env python
"""Recursively import local media into the AzuraCast radio pipeline."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests

DIRECT_EXTS = {".mp3"}
CONVERT_EXTS = {".mp4", ".webm"}
SKIP_EXTS = {".flac"}


@dataclass
class Candidate:
    source: Path
    mode: str  # direct | convert | skip
    reason: str


def discover_ffmpeg(explicit: str | None) -> str:
    candidates = [
        explicit,
        os.environ.get("FFMPEG_PATH"),
        r"C:\Program Files\ShareX\ffmpeg.exe",
        r"D:\Remotion Demo\grest-explainer\node_modules\@remotion\compositor-win32-x64-msvc\ffmpeg.exe",
        "ffmpeg",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return str(path)
        if candidate == "ffmpeg":
            try:
                subprocess.run([candidate, "-version"], capture_output=True, check=True)
                return candidate
            except Exception:
                pass
    raise SystemExit("ffmpeg not found. Pass --ffmpeg-path or set FFMPEG_PATH.")


def iter_candidates(root: Path) -> Iterable[Candidate]:
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        ext = path.suffix.lower()
        if ext in DIRECT_EXTS:
            yield Candidate(path, "direct", "mp3")
        elif ext in CONVERT_EXTS:
            yield Candidate(path, "convert", ext.lstrip("."))
        elif ext in SKIP_EXTS:
            yield Candidate(path, "skip", "flac")
        else:
            yield Candidate(path, "skip", f"unsupported:{ext or 'noext'}")


def convert_to_mp3(ffmpeg: str, source: Path, temp_dir: Path) -> Path:
    dest = temp_dir / f"{source.stem}.mp3"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def upload_mp3(api_url: str, api_key: str, file_path: Path, replace: bool) -> dict:
    url = api_url.rstrip("/") + "/radio/upload"
    with file_path.open("rb") as handle:
        resp = requests.post(
            url,
            headers={"X-API-Key": api_key},
            files={"file": (file_path.name, handle, "audio/mpeg")},
            data={"replace": str(replace).lower()},
            timeout=300,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"{resp.status_code} {resp.text}")
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", help="Root folder to scan recursively")
    parser.add_argument("--api-url", required=True, help="Media API base URL")
    parser.add_argument("--api-key", required=True, help="Media API X-API-Key")
    parser.add_argument("--ffmpeg-path", help="Path to ffmpeg executable")
    parser.add_argument("--dry-run", action="store_true", help="Only report candidates")
    parser.add_argument("--replace", action="store_true", help="Replace existing radio file if present")
    args = parser.parse_args()

    root = Path(args.source_dir).expanduser()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Source directory not found: {root}")

    ffmpeg = None if args.dry_run else discover_ffmpeg(args.ffmpeg_path)

    candidates = list(iter_candidates(root))
    report = {
        "source_dir": str(root),
        "direct": [],
        "convert": [],
        "skipped": [],
        "uploaded": [],
        "failed": [],
    }

    for item in candidates:
        record = {"path": str(item.source), "reason": item.reason}
        if item.mode == "skip":
            report["skipped"].append(record)
        elif item.mode == "direct":
            report["direct"].append(record)
        else:
            report["convert"].append(record)

    if args.dry_run:
        print(json.dumps(report, indent=2))
        return 0

    with tempfile.TemporaryDirectory(prefix="radio_audio_import_") as temp_dir_str:
        temp_dir = Path(temp_dir_str)

        for item in candidates:
            if item.mode == "skip":
                continue

            working_path = item.source
            try:
                if item.mode == "convert":
                    working_path = convert_to_mp3(ffmpeg, item.source, temp_dir)

                result = upload_mp3(args.api_url, args.api_key, working_path, args.replace)
                report["uploaded"].append(
                    {
                        "source": str(item.source),
                        "mode": item.mode,
                        "uploaded_as": result.get("azuracast_path"),
                        "file_id": result.get("azuracast_file_id"),
                    }
                )
            except Exception as exc:
                report["failed"].append(
                    {
                        "source": str(item.source),
                        "mode": item.mode,
                        "error": str(exc),
                    }
                )

    print(json.dumps(report, indent=2))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
