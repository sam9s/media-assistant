#!/usr/bin/env python
"""Batch import a mixed FLAC tree into the manual music pipeline."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


TRACK_BUCKET_DIRS = {"telegram"}
DISC_RE = re.compile(r"(cd|disc|disk)\s*[_ -]*\d+$", re.IGNORECASE)


def is_disc_dir(path: Path) -> bool:
    return bool(DISC_RE.fullmatch(path.name))


def flac_count(path: Path) -> int:
    return sum(1 for _ in path.glob("*.flac"))


def child_dirs_with_flacs(path: Path) -> list[Path]:
    return [child for child in path.iterdir() if child.is_dir() and any(child.glob("*.flac"))]


def discover_units(root: Path) -> tuple[list[dict], list[dict]]:
    units: list[dict] = []
    skipped: list[dict] = []
    seen_album_dirs: set[Path] = set()

    for current in sorted(p for p in root.rglob("*") if p.is_dir()):
        direct_flacs = list(current.glob("*.flac"))
        if not direct_flacs:
            continue

        lower_name = current.name.lower()
        if lower_name in TRACK_BUCKET_DIRS:
            for flac in sorted(direct_flacs):
                units.append({"mode": "track", "path": flac})
            continue

        if is_disc_dir(current):
            parent = current.parent
            disc_siblings = [d for d in parent.iterdir() if d.is_dir() and is_disc_dir(d) and any(d.glob("*.flac"))]
            if len(disc_siblings) >= 2 and parent != root and parent.parent != root:
                if parent not in seen_album_dirs:
                    units.append({"mode": "album", "path": parent})
                    seen_album_dirs.add(parent)
            else:
                skipped.append({"path": current, "reason": "ambiguous-disc-dir"})
            continue

        child_flac_dirs = child_dirs_with_flacs(current)
        if child_flac_dirs:
            disc_children = [d for d in child_flac_dirs if is_disc_dir(d)]
            if len(disc_children) >= 2:
                if current not in seen_album_dirs:
                    units.append({"mode": "album", "path": current})
                    seen_album_dirs.add(current)
                continue
            skipped.append({"path": current, "reason": "nested-flac-subdirs"})
            continue

        if current not in seen_album_dirs:
            units.append({"mode": "album", "path": current})
            seen_album_dirs.add(current)

    return units, skipped


def run_import(
    helper_script: Path,
    source_path: Path,
    language: str,
    api_url: str,
    api_key: str,
    ssh_host: str,
    replace_existing: bool,
    mode: str,
) -> dict:
    cmd = [
        sys.executable,
        str(helper_script),
        str(source_path),
        "--language",
        language,
        "--api-url",
        api_url,
        "--api-key",
        api_key,
        "--ssh-host",
        ssh_host,
        "--mode",
        mode,
        "--wait",
    ]
    if replace_existing:
        cmd.append("--replace-existing")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    stdout = proc.stdout.strip() or "{}"
    result = json.loads(stdout)
    result["_returncode"] = proc.returncode
    if proc.stderr.strip():
        result["_stderr"] = proc.stderr.strip()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root")
    parser.add_argument("--language", default="auto", choices=["english", "hindi", "punjabi", "auto"])
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--ssh-host", required=True)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(args.source_root).expanduser()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"Source root not found: {root}")

    helper_script = Path(__file__).with_name("manual_import_music.py")
    units, skipped = discover_units(root)

    report: dict = {
        "source_root": str(root),
        "albums": sum(1 for u in units if u["mode"] == "album"),
        "tracks": sum(1 for u in units if u["mode"] == "track"),
        "skipped": [{"path": str(item["path"]), "reason": item["reason"]} for item in skipped],
        "results": [],
        "failed": [],
    }

    if args.dry_run:
        report["units"] = [{"mode": item["mode"], "path": str(item["path"])} for item in units]
        sys.stdout.buffer.write((json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8", errors="replace"))
        return 0

    for item in units:
        result = run_import(
            helper_script=helper_script,
            source_path=item["path"],
            language=args.language,
            api_url=args.api_url,
            api_key=args.api_key,
            ssh_host=args.ssh_host,
            replace_existing=args.replace_existing,
            mode=item["mode"],
        )
        entry = {
            "mode": item["mode"],
            "path": str(item["path"]),
            "status": result.get("status", {}).get("status"),
            "resolved_language": result.get("status", {}).get("language"),
            "message": result.get("status", {}).get("message"),
            "returncode": result.get("_returncode"),
        }
        if result.get("_returncode") == 0:
            report["results"].append(entry)
        else:
            entry["raw"] = result
            report["failed"].append(entry)

    sys.stdout.buffer.write((json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8", errors="replace"))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
