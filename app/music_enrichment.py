"""
Music enrichment pipeline.

Called after slskd finishes a download. Runs in a background asyncio task so the
/music/complete webhook returns 200 immediately without blocking slskd.

Flow:
  1. AcoustID fingerprint first FLAC → MusicBrainz Release ID (MBID)
  2. MusicBrainz → Artist, Album, Year, track count
  3. TheAudioDB → front cover URL
  4. Fanart.tv → CD art URL, ClearArt logo URL
  5. mutagen → embed cover art into every FLAC header
  6. Save folder.jpg / cd.png / logo.png in album folder
  7. Rename folder to "Artist - Album (Year) [FLAC]"  (or "[FLAC 24bit]" for hi-res)
  8. Move enriched folder to /mnt/cloud/gdrive/Media/Music/{language}/
  9. Trigger Navidrome scan
"""

import asyncio
import json
import hashlib
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Optional

import acoustid
import httpx
import musicbrainzngs
from mutagen.flac import FLAC, Picture

from app.config import settings
from app.navidrome import detect_stale_entries, trigger_scan

logger = logging.getLogger("uvicorn.error")

musicbrainzngs.set_useragent("SamAssist", "3.0", "sam@sam9scloud.in")

MUSIC_ROOT = "/mnt/cloud/gdrive/Media/Music"
MANUAL_IMPORT_BACKUP_ROOT = f"{MUSIC_ROOT}/Downloads/manual_import_replaced"
LANGUAGE_DIRS = {
    "english": f"{MUSIC_ROOT}/English",
    "hindi":   f"{MUSIC_ROOT}/Hindi",
    "punjabi": f"{MUSIC_ROOT}/Punjabi",
}

PUNJABI_KEYWORDS = {
    "karan aujla", "diljit", "diljit dosanjh", "sidhu moose wala",
    "ammy virk", "gurdas maan", "ap dhillon", "shubh", "guru randhawa",
    "jazzy b", "yo yo honey singh", "harrdy sandhu", "nimrat khaira",
    "navaan sandhu", "kulwinder billa", "arjan dhillon",
}

HINDI_KEYWORDS = {
    "kishore kumar", "mohammed rafi", "asha bhosle", "lata mangeshkar",
    "rd burman", "r d burman", "rahul dev burman", "bappi lahiri",
    "a.r. rahman", "ar rahman", "alka yagnik", "anuradha paudwal",
    "udit narayan", "kumar sanu", "sonu nigam", "shreya ghoshal",
    "sunidhi chauhan", "jagjit singh", "javed ali", "hemant kumar",
    "amitabh bachchan", "adnan sami", "atif aslam", "benny dayal",
    "mohit chauhan", "rahat fateh ali khan", "abhijeet", "sukhwinder singh",
    "anu malik", "laxmikant", "pyarelal", "bollywood", "hindi song",
    "qayamat se qayamat tak", "amar akbar anthony", "umrao jaan",
    "rockstar", "deewaar", "saagar", "bazaar", "utsav", "qurbani",
    "khalnayak", "namak halaal", "anjaam", "shalimar", "disco dancer",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    """Strip characters that are invalid in Linux filenames."""
    name = re.sub(r'[<>:"/\\?*\x00-\x1f|]', "", name)
    return re.sub(r" {2,}", " ", name).strip()


def _backup_existing_path(path: Path) -> Path:
    backup_root = Path(MANUAL_IMPORT_BACKUP_ROOT)
    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    target = backup_root / f"{timestamp}-{path.name}"
    counter = 1
    while target.exists():
        target = backup_root / f"{timestamp}-{counter}-{path.name}"
        counter += 1
    shutil.move(str(path), str(target))
    return target


def _remove_path_quietly(path: Path) -> None:
    try:
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except Exception as e:
        logger.warning("Cleanup failed for %s: %s", path, e)


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _contains_keyword(texts: list[str], keywords: set[str]) -> bool:
    haystack = " | ".join((t or "").lower() for t in texts)
    return any(keyword in haystack for keyword in keywords)


async def _detect_album_language(
    flac_files: list[Path],
    artist: str,
    album: str,
    artist_hint: str = "",
    album_hint: str = "",
) -> str:
    sample_texts = [artist, album, artist_hint, album_hint]
    for flac in flac_files[:3]:
        hints = _read_album_hints(flac)
        sample_texts.extend([hints.get("artist", ""), hints.get("album", ""), flac.stem, flac.parent.name])

    if _contains_keyword(sample_texts, PUNJABI_KEYWORDS):
        return "punjabi"
    if _contains_keyword(sample_texts, HINDI_KEYWORDS):
        return "hindi"
    return "english"


async def _detect_track_language(
    flac_path: Path,
    artist: str,
    title: str,
    artist_hint: str = "",
    title_hint: str = "",
) -> str:
    hints = _read_track_hints(flac_path)
    sample_texts = [
        artist,
        title,
        artist_hint,
        title_hint,
        hints.get("artist", ""),
        hints.get("title", ""),
        flac_path.stem,
        flac_path.parent.name,
    ]
    if _contains_keyword(sample_texts, PUNJABI_KEYWORDS):
        return "punjabi"
    if _contains_keyword(sample_texts, HINDI_KEYWORDS):
        return "hindi"
    return "english"


def _is_disc_dir(name: str) -> bool:
    return bool(re.fullmatch(r"(cd|disc|disk)\s*[_ -]*\d+", name.strip(), re.IGNORECASE))


def _album_root_from_flacs(download_root: Path, flac_files: list[Path]) -> Path:
    if not flac_files:
        return download_root
    common = Path(os.path.commonpath([str(f.parent) for f in flac_files]))
    if _is_disc_dir(common.name) and common.parent != common:
        return common.parent
    if download_root == common or download_root in common.parents:
        return common
    return download_root


def _read_album_hints(flac_path: Path) -> dict:
    try:
        audio = FLAC(str(flac_path))
    except Exception:
        return {}

    def _first(*keys: str) -> str:
        for key in keys:
            value = audio.get(key)
            if value:
                return str(value[0]).strip()
        return ""

    date = _first("date", "originaldate", "year")
    year_match = re.search(r"\b(19|20)\d{2}\b", date)
    return {
        "artist": _first("albumartist", "artist"),
        "album": _first("album"),
        "year": year_match.group(0) if year_match else "",
    }


def _read_track_hints(flac_path: Path) -> dict:
    try:
        audio = FLAC(str(flac_path))
    except Exception:
        return {}

    def _first(*keys: str) -> str:
        for key in keys:
            value = audio.get(key)
            if value:
                return str(value[0]).strip()
        return ""

    return {
        "artist": _first("artist", "albumartist"),
        "title": _first("title"),
    }


def _guess_artist_title(value: str) -> tuple[str, str]:
    text = (value or "").strip()
    if not text:
        return "", ""
    separators = [" - ", " – ", " — ", "_-_"]
    for sep in separators:
        if sep in text:
            left, right = text.split(sep, 1)
            artist = left.strip(" -_")
            title = right.strip(" -_")
            if artist and title:
                return artist, title
    return "", text


def _guess_album_year_from_parent(parent: Path) -> tuple[str, str]:
    name = parent.name.strip()
    year = ""
    album = name
    m = re.match(r"^(?P<year>(19|20)\d{2})\s*[-_]\s*(?P<album>.+)$", name)
    if m:
        year = m.group("year")
        album = m.group("album").strip()
    return album, year


def _write_track_tags(
    flac_path: str,
    *,
    artist: str,
    title: str,
    album: str = "",
    albumartist: str = "",
    year: str = "",
) -> None:
    try:
        audio = FLAC(flac_path)
        if artist:
            audio["artist"] = [artist]
        if title:
            audio["title"] = [title]
        if album:
            audio["album"] = [album]
        if albumartist:
            audio["albumartist"] = [albumartist]
        if year:
            audio["date"] = [year]
        audio.save()
    except Exception as e:
        logger.warning("Track tag write failed for %s: %s", flac_path, e)


def _find_existing_album_dir(dest_root: Path, artist: str, album: str) -> Optional[Path]:
    want_album = _norm_text(album)
    want_artist = _norm_text(artist)
    if not want_album or not dest_root.exists():
        return None

    for child in dest_root.iterdir():
        if not child.is_dir() or child.name.lower() == "misc":
            continue
        flacs = sorted(child.rglob("*.flac"))
        if not flacs:
            continue
        hints = _read_album_hints(flacs[0])
        existing_album = _norm_text(hints.get("album") or child.name)
        existing_artist = _norm_text(hints.get("artist") or child.name)
        if existing_album != want_album:
            continue
        if not want_artist or not existing_artist or want_artist == existing_artist:
            return child
        if want_artist == "variousartists" or existing_artist == "variousartists":
            return child
    return None


def _file_md5(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _album_signature(root: Path) -> dict[str, str]:
    signature: dict[str, str] = {}
    for flac in sorted(root.rglob("*.flac")):
        rel = flac.relative_to(root).as_posix().lower()
        signature[rel] = _file_md5(flac)
    return signature


def _albums_are_exact_duplicates(source_root: Path, existing_root: Path) -> bool:
    if not source_root.exists() or not existing_root.exists():
        return False
    source_sig = _album_signature(source_root)
    existing_sig = _album_signature(existing_root)
    return bool(source_sig) and source_sig == existing_sig


def _unique_album_destination(dest_root: Path, folder_name: str) -> Path:
    base = dest_root / folder_name
    if not base.exists():
        return base
    counter = 2
    while True:
        candidate = dest_root / f"{folder_name} [{counter}]"
        if not candidate.exists():
            return candidate
        counter += 1


def _single_track_exists(dest_root: Path, artist: str, title: str, language: str) -> Optional[Path]:
    if language.lower() == "punjabi":
        search_root = dest_root
    else:
        search_root = dest_root / "Misc"
    if not search_root.exists():
        return None
    want_artist = _norm_text(artist)
    want_title = _norm_text(title)
    for flac in search_root.rglob("*.flac"):
        hints = _read_track_hints(flac)
        existing_artist = _norm_text(hints.get("artist") or flac.stem)
        existing_title = _norm_text(hints.get("title") or flac.stem)
        if existing_title == want_title and (
            not want_artist or not existing_artist or existing_artist == want_artist
        ):
            return flac
    return None


async def _fetch_bytes(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code == 200 and len(r.content) > 1000:
                return r.content
    except Exception as e:
        logger.warning("Image fetch failed %s: %s", url, e)
    return None


# ---------------------------------------------------------------------------
# Step 1: AcoustID fingerprint → MBID
# ---------------------------------------------------------------------------

async def _fingerprint_to_mbid(flac_path: str) -> Optional[str]:
    """Run fpcalc in a child process, query AcoustID, return the top MusicBrainz recording ID.

    Important: do not call acoustid.fingerprint_file here. Some files can trigger a
    chromaprint assertion abort that kills the worker process. Running fpcalc as a
    separate subprocess keeps failures contained and lets the pipeline fall back.
    """
    if not settings.ACOUSTID_API_KEY:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "fpcalc",
            "-json",
            flac_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "fpcalc failed for %s: rc=%s stderr=%s",
                flac_path,
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
            return None
        data = json.loads(stdout.decode(errors="replace") or "{}")
        duration = data.get("duration")
        fingerprint = data.get("fingerprint")
        if not duration or not fingerprint:
            logger.warning("fpcalc produced no usable fingerprint for %s", flac_path)
            return None
        results = await asyncio.to_thread(
            acoustid.lookup,
            settings.ACOUSTID_API_KEY,
            fingerprint,
            duration,
            meta=["recordings", "releasegroups"],
        )
        for score, recording_id, title, artist in acoustid.parse_lookup_result(results):
            if score > 0.5 and recording_id:
                return recording_id
    except Exception as e:
        logger.warning("AcoustID fingerprint error for %s: %s", flac_path, e)
    return None


# ---------------------------------------------------------------------------
# Step 2: MusicBrainz → release metadata + Release Group ID
# ---------------------------------------------------------------------------

def _mb_release_from_recording(recording_id: str) -> Optional[dict]:
    """Look up a recording to find release metadata. Returns a dict with keys:
    artist, album, year, release_group_id."""
    try:
        result = musicbrainzngs.get_recording_by_id(
            recording_id,
            includes=["artists", "releases"],
        )
        recording = result.get("recording", {})
        artist = ""
        credit = recording.get("artist-credit", [])
        if credit:
            first = credit[0]
            if isinstance(first, dict):
                artist = first.get("artist", {}).get("name", "")

        releases = recording.get("release-list", [])
        if not releases:
            return None
        rel = releases[0]
        album = rel.get("title", "")
        year = rel.get("date", "")[:4]
        rg = rel.get("release-group", {})
        rg_id = rg.get("id", "")
        return {"artist": artist, "album": album, "year": year, "release_group_id": rg_id}
    except Exception as e:
        logger.warning("MusicBrainz lookup error for recording %s: %s", recording_id, e)
        return None


# ---------------------------------------------------------------------------
# Step 3: TheAudioDB → front cover URL
# ---------------------------------------------------------------------------

async def _theaudiodb_cover(artist: str, album: str) -> Optional[str]:
    key = settings.THEAUDIODB_API_KEY or "2"
    url = f"https://www.theaudiodb.com/api/v1/json/{key}/searchalbum.php"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params={"s": artist, "a": album})
        data = r.json().get("album") or []
        for a in data:
            cover = a.get("strAlbumThumb") or a.get("strAlbumThumbHQ")
            if cover:
                return cover
    except Exception as e:
        logger.warning("TheAudioDB cover error: %s", e)
    return None


# ---------------------------------------------------------------------------
# Step 4: Fanart.tv → CD art + ClearArt logo (needs MusicBrainz Release Group ID)
# ---------------------------------------------------------------------------

async def _fanart_tv_art(release_group_id: str) -> dict:
    """Returns {cd: url|None, logo: url|None}"""
    result = {"cd": None, "logo": None}
    if not settings.FANART_TV_API_KEY or not release_group_id:
        return result
    url = f"https://webservice.fanart.tv/v3/music/{release_group_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params={"api_key": settings.FANART_TV_API_KEY})
        data = r.json()
        cd_list = data.get("cdart", [])
        if cd_list:
            result["cd"] = cd_list[0].get("url")
        logo_list = data.get("musiclogo", [])
        if logo_list:
            result["logo"] = logo_list[0].get("url")
    except Exception as e:
        logger.warning("Fanart.tv error for %s: %s", release_group_id, e)
    return result


# ---------------------------------------------------------------------------
# Step 5: Embed cover art into FLAC files via mutagen
# ---------------------------------------------------------------------------

def _embed_cover_into_flac(flac_path: str, cover_bytes: bytes) -> None:
    try:
        audio = FLAC(flac_path)
        pic = Picture()
        pic.type = 3  # Front cover
        pic.mime = "image/jpeg"
        pic.data = cover_bytes
        audio.clear_pictures()
        audio.add_picture(pic)
        audio.save()
    except Exception as e:
        logger.warning("mutagen embed error for %s: %s", flac_path, e)


# ---------------------------------------------------------------------------
# Main enrichment entry point
# ---------------------------------------------------------------------------

async def enrich_and_deliver(
    download_folder: str,
    language: str,
    artist_hint: str = "",
    album_hint: str = "",
    replace_existing: bool = False,
) -> dict:
    """
    Full enrichment pipeline. Runs as a background task.

    :param download_folder: Absolute path to the folder containing downloaded FLAC files.
    :param language: "english" | "hindi" | "punjabi"
    :param artist_hint: Artist name from the search result (fallback if AcoustID fails)
    :param album_hint:  Album name from the search result (fallback if AcoustID fails)
    """
    logger.info("Enrichment started: %s → %s", download_folder, language)

    folder = Path(download_folder)
    old_flac_paths = [str(p) for p in sorted(folder.rglob("*.flac"))]

    if not folder.exists():
        logger.error("Enrichment: download folder not found: %s", download_folder)
        return {"success": False, "message": "download folder not found"}

    flac_files = sorted(folder.rglob("*.flac"))
    if not flac_files:
        logger.warning("Enrichment: no FLAC files found in %s", download_folder)
        return {"success": False, "message": "no FLAC files found"}

    album_root = _album_root_from_flacs(folder, flac_files)
    tag_hints = _read_album_hints(flac_files[0])

    # --- Step 1+2: Fingerprint first FLAC → metadata ---
    meta = None
    recording_id = await _fingerprint_to_mbid(str(flac_files[0]))
    if recording_id:
        meta = await asyncio.to_thread(_mb_release_from_recording, recording_id)

    artist = (meta or {}).get("artist") or artist_hint or tag_hints.get("artist") or "Unknown Artist"
    album  = (meta or {}).get("album")  or album_hint  or tag_hints.get("album") or album_root.name
    year   = (meta or {}).get("year")   or tag_hints.get("year") or ""
    rg_id  = (meta or {}).get("release_group_id", "")
    resolved_language = language.lower()
    if resolved_language == "auto":
        resolved_language = await _detect_album_language(flac_files, artist, album, artist_hint, album_hint)
    dest_root = LANGUAGE_DIRS.get(resolved_language, LANGUAGE_DIRS["english"])

    # Detect hi-res (any file > 16-bit)
    is_hires = _detect_hires(flac_files)
    quality_tag = "FLAC 24bit" if is_hires else "FLAC"

    logger.info("Enrichment metadata: artist=%r album=%r year=%r hires=%s", artist, album, year, is_hires)

    existing_dir = _find_existing_album_dir(Path(dest_root), artist, album)
    year_part = f" ({year})" if year else ""
    folder_name = _safe_name(f"{artist} - {album}{year_part} [{quality_tag}]")
    dest_root_path = Path(dest_root)
    dest = dest_root_path / folder_name

    if existing_dir and not replace_existing:
        if _albums_are_exact_duplicates(album_root, existing_dir):
            message = f"Exact duplicate album already exists: {existing_dir}"
            logger.info("Album duplicate detected via file signature, skipping delivery: %s", message)
            _remove_path_quietly(album_root)
            return {
                "success": True,
                "duplicate": True,
                "message": message,
                "destination": str(existing_dir),
                "language": resolved_language,
            }
        logger.info(
            "Album metadata matched existing folder but file signatures differ; treating as distinct edition: %s",
            existing_dir,
        )
        dest = _unique_album_destination(dest_root_path, folder_name)

    if replace_existing and existing_dir and existing_dir != dest and dest.exists():
        backup = _backup_existing_path(existing_dir)
        message = f"Removed older duplicate {existing_dir}; enriched copy already exists at {dest}"
        logger.info("Album upgrade cleanup complete: backup=%s", backup)
        return {
            "success": True,
            "duplicate": False,
            "message": message,
            "destination": str(dest),
            "backup": str(backup),
            "language": resolved_language,
        }

    # --- Step 3+4: Fetch art ---
    cover_bytes = None
    cover_url = await _theaudiodb_cover(artist, album)
    if cover_url:
        cover_bytes = await _fetch_bytes(cover_url)

    fanart = await _fanart_tv_art(rg_id)
    cd_bytes   = await _fetch_bytes(fanart["cd"])   if fanart["cd"]   else None
    logo_bytes = await _fetch_bytes(fanart["logo"]) if fanart["logo"] else None

    # --- Step 5: Embed cover + save art files ---
    if cover_bytes:
        for f in flac_files:
            await asyncio.to_thread(_embed_cover_into_flac, str(f), cover_bytes)

    art_dir = album_root
    if cover_bytes:
        (art_dir / "folder.jpg").write_bytes(cover_bytes)
    if cd_bytes:
        (art_dir / "cd.png").write_bytes(cd_bytes)
    if logo_bytes:
        (art_dir / "logo.png").write_bytes(logo_bytes)

    # --- Step 7: Rename the folder ---
    renamed = album_root.parent / folder_name
    delivery_root = album_root
    if album_root != renamed:
        try:
            album_root.rename(renamed)
            delivery_root = renamed
        except Exception as e:
            logger.warning("Folder rename failed: %s", e)

    # --- Step 8: Move to destination ---
    try:
        if replace_existing and existing_dir and existing_dir.exists() and existing_dir != dest:
            backup = _backup_existing_path(existing_dir)
            logger.info("Album upgrade backed up existing folder %s -> %s", existing_dir, backup)

        if replace_existing and dest.exists():
            backup = _backup_existing_path(dest)
            logger.info("Album upgrade backed up destination folder %s -> %s", dest, backup)

        if dest.exists():
            if _albums_are_exact_duplicates(delivery_root, dest):
                logger.warning("Destination already exists with exact matching files, skipping move: %s", dest)
                _remove_path_quietly(delivery_root)
                return {
                    "success": True,
                    "duplicate": True,
                    "message": f"Exact duplicate album already exists: {dest}",
                    "destination": str(dest),
                    "language": resolved_language,
                }
            unique_dest = _unique_album_destination(dest_root_path, folder_name)
            logger.info("Destination %s already exists with different files; using %s", dest, unique_dest)
            dest = unique_dest

        shutil.move(str(delivery_root), str(dest))
        logger.info("Enrichment delivered: %s", dest)
    except Exception as e:
        logger.error("Enrichment move failed %s → %s: %s", delivery_root, dest, e)
        return {"success": False, "message": f"move failed: {e}", "language": resolved_language}

    # --- Step 9: Navidrome scan ---
    scan_result = await trigger_scan()
    logger.info("Navidrome scan: %s", scan_result)
    await asyncio.sleep(5)
    stale = await detect_stale_entries(old_flac_paths)
    message = None
    if stale.get("stale_found"):
        message = (
            f"Navidrome stale-entry check found {len(stale.get('stale_media') or [])} missing old track(s), "
            f"{len(stale.get('orphan_albums') or [])} orphan album row(s), "
            f"{len(stale.get('orphan_artists') or [])} orphan artist row(s). Cleanup review is recommended."
        )
    return {
        "success": True,
        "duplicate": False,
        "destination": str(dest),
        "scan": scan_result,
        "language": resolved_language,
        "stale_check": stale,
        "message": message,
    }


# ---------------------------------------------------------------------------
# Single-track helpers
# ---------------------------------------------------------------------------

def _mb_recording_meta(recording_id: str) -> Optional[dict]:
    """Look up a MusicBrainz recording to get track title + artist.
    Returns {"artist": str, "title": str} — distinct from _mb_release_from_recording
    which returns album-level data."""
    try:
        result = musicbrainzngs.get_recording_by_id(
            recording_id,
            includes=["artists"],
        )
        recording = result.get("recording", {})
        title = recording.get("title", "")
        artist = ""
        credit = recording.get("artist-credit", [])
        if credit:
            first = credit[0]
            if isinstance(first, dict):
                artist = first.get("artist", {}).get("name", "")
        if title or artist:
            return {"artist": artist, "title": title}
    except Exception as e:
        logger.warning("MusicBrainz recording meta error for %s: %s", recording_id, e)
    return None


async def _theaudiodb_track_cover(artist: str, title: str) -> Optional[str]:
    """Search TheAudioDB for a track and return the first available thumb URL."""
    key = settings.THEAUDIODB_API_KEY or "2"
    url = f"https://www.theaudiodb.com/api/v1/json/{key}/searchtrack.php"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params={"s": artist, "t": title})
        tracks = r.json().get("track") or []
        for t in tracks:
            cover = t.get("strTrackThumb") or t.get("strAlbumThumb")
            if cover:
                return cover
    except Exception as e:
        logger.warning("TheAudioDB track cover error: %s", e)
    return None


async def enrich_single_track(
    flac_path: str,
    language: str,
    title_hint: str = "",
    artist_hint: str = "",
    replace_existing: bool = False,
) -> dict:
    """
    Enrich and deliver a single FLAC track to the Misc/ folder.

    :param flac_path: Absolute path to the FLAC file.
    :param language: "english" | "hindi" | "punjabi"
    :param title_hint: Track title fallback if AcoustID fails.
    :param artist_hint: Artist name fallback if AcoustID fails.
    """
    logger.info("Single-track enrichment started: %s → %s/Misc", flac_path, language)

    file = Path(flac_path)
    old_track_path = str(file)
    if not file.exists():
        logger.error("Track enrichment: file not found: %s", flac_path)
        return {"success": False, "message": "file not found"}

    # --- Step 1+2: Fingerprint → MBID → recording title + artist ---
    recording_id = await _fingerprint_to_mbid(flac_path)
    meta = None
    if recording_id:
        meta = await asyncio.to_thread(_mb_recording_meta, recording_id)

    tag_hints = _read_track_hints(file)
    parsed_artist = ""
    parsed_title = ""
    for candidate in (title_hint, file.stem):
        parsed_artist, parsed_title = _guess_artist_title(candidate)
        if parsed_artist and parsed_title:
            break

    artist = (meta or {}).get("artist") or tag_hints.get("artist") or parsed_artist or artist_hint or "Unknown Artist"
    title  = (meta or {}).get("title")  or tag_hints.get("title") or parsed_title or title_hint or file.stem
    album_hint_from_parent, year_hint_from_parent = _guess_album_year_from_parent(file.parent)
    album = album_hint_from_parent if album_hint_from_parent and album_hint_from_parent != file.parent.name else ""
    year = year_hint_from_parent
    resolved_language = language.lower()
    if resolved_language == "auto":
        resolved_language = await _detect_track_language(file, artist, title, artist_hint, title_hint)
    dest_root = LANGUAGE_DIRS.get(resolved_language, LANGUAGE_DIRS["english"])

    logger.info("Track enrichment metadata: artist=%r title=%r album=%r year=%r", artist, title, album, year)

    existing_file = _single_track_exists(Path(dest_root), artist, title, language)
    if existing_file and not replace_existing:
        message = f"Track already exists: {existing_file}"
        logger.info("Track duplicate detected, skipping manual delivery: %s", message)
        return {
            "success": True,
            "duplicate": True,
            "message": message,
            "destination": str(existing_file),
            "language": resolved_language,
        }

    # --- Step 3: Fetch cover art ---
    cover_bytes = None
    cover_url = await _theaudiodb_track_cover(artist, title)
    if not cover_url and album:
        cover_url = await _theaudiodb_cover(artist, album)
    if cover_url:
        cover_bytes = await _fetch_bytes(cover_url)

    # --- Step 4: Write core tags and embed cover into FLAC ---
    await asyncio.to_thread(
        _write_track_tags,
        flac_path,
        artist=artist,
        title=title,
        album=album,
        albumartist=artist,
        year=year,
    )
    if cover_bytes:
        await asyncio.to_thread(_embed_cover_into_flac, flac_path, cover_bytes)

    # --- Step 5: Rename file ---
    safe_filename = _safe_name(f"{artist} - {title}.flac")
    renamed = file.parent / safe_filename
    if file != renamed:
        try:
            file.rename(renamed)
            file = renamed
        except Exception as e:
            logger.warning("Track rename failed: %s", e)

    # --- Step 6: Determine destination directory ---
    # Punjabi single tracks go directly into Punjabi/ (no Misc subfolder).
    # English and Hindi single tracks always go into {language}/Misc/.
    if resolved_language == "punjabi":
        dest_dir = Path(dest_root)
    else:
        dest_dir = Path(dest_root) / "Misc"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # --- Step 7: Move to destination ---
    dest = dest_dir / file.name
    try:
        if replace_existing and existing_file and existing_file.exists() and existing_file != dest:
            backup = _backup_existing_path(existing_file)
            logger.info("Track upgrade backed up existing file %s -> %s", existing_file, backup)

        if replace_existing and dest.exists():
            backup = _backup_existing_path(dest)
            logger.info("Track upgrade backed up destination file %s -> %s", dest, backup)

        if dest.exists():
            logger.warning("Destination already exists, skipping: %s", dest)
            return {
                "success": True,
                "duplicate": True,
                "message": f"Destination already exists: {dest}",
                "destination": str(dest),
                "language": resolved_language,
            }

        shutil.move(str(file), str(dest))
        logger.info("Track enrichment delivered: %s", dest)
    except Exception as e:
        logger.error("Track move failed %s → %s: %s", file, dest, e)
        return {"success": False, "message": f"move failed: {e}", "language": resolved_language}

    # --- Step 8: Navidrome scan ---
    scan_result = await trigger_scan()
    logger.info("Navidrome scan after track delivery: %s", scan_result)
    await asyncio.sleep(5)
    stale = await detect_stale_entries([old_track_path])
    message = None
    if stale.get("stale_found"):
        message = (
            f"Navidrome stale-entry check found {len(stale.get('stale_media') or [])} missing old track(s), "
            f"{len(stale.get('orphan_albums') or [])} orphan album row(s), "
            f"{len(stale.get('orphan_artists') or [])} orphan artist row(s). Cleanup review is recommended."
        )
    return {
        "success": True,
        "duplicate": False,
        "destination": str(dest),
        "scan": scan_result,
        "language": resolved_language,
        "stale_check": stale,
        "message": message,
    }


def _detect_hires(flac_files: list) -> bool:
    """Check if any FLAC file has > 16-bit depth."""
    for f in flac_files:
        try:
            audio = FLAC(str(f))
            info = audio.info
            if hasattr(info, "bits_per_sample") and info.bits_per_sample > 16:
                return True
            if hasattr(info, "sample_rate") and info.sample_rate > 48000:
                return True
        except Exception:
            pass
    return False
