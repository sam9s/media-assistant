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
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

import acoustid
import httpx
import musicbrainzngs
from mutagen.flac import FLAC, Picture

from app.config import settings
from app.navidrome import trigger_scan

logger = logging.getLogger("uvicorn.error")

musicbrainzngs.set_useragent("SamAssist", "3.0", "sam@sam9scloud.in")

MUSIC_ROOT = "/mnt/cloud/gdrive/Media/Music"
LANGUAGE_DIRS = {
    "english": f"{MUSIC_ROOT}/English",
    "hindi":   f"{MUSIC_ROOT}/Hindi",
    "punjabi": f"{MUSIC_ROOT}/Punjabi",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(name: str) -> str:
    """Strip characters that are invalid in Linux filenames."""
    name = re.sub(r'[<>:"/\\?*\x00-\x1f|]', "", name)
    return re.sub(r" {2,}", " ", name).strip()


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
    """Run fpcalc on the file, query AcoustID, return the top MusicBrainz recording ID."""
    if not settings.ACOUSTID_API_KEY:
        return None
    try:
        # fpcalc is a blocking subprocess — run in thread pool
        duration, fingerprint = await asyncio.to_thread(
            acoustid.fingerprint_file, flac_path
        )
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
) -> bool:
    """
    Full enrichment pipeline. Runs as a background task.

    :param download_folder: Absolute path to the folder containing downloaded FLAC files.
    :param language: "english" | "hindi" | "punjabi"
    :param artist_hint: Artist name from the search result (fallback if AcoustID fails)
    :param album_hint:  Album name from the search result (fallback if AcoustID fails)
    """
    logger.info("Enrichment started: %s → %s", download_folder, language)

    dest_root = LANGUAGE_DIRS.get(language.lower(), LANGUAGE_DIRS["english"])
    folder = Path(download_folder)

    if not folder.exists():
        logger.error("Enrichment: download folder not found: %s", download_folder)
        return False

    flac_files = sorted(folder.rglob("*.flac"))
    if not flac_files:
        logger.warning("Enrichment: no FLAC files found in %s", download_folder)
        return False

    # --- Step 1+2: Fingerprint first FLAC → metadata ---
    meta = None
    recording_id = await _fingerprint_to_mbid(str(flac_files[0]))
    if recording_id:
        meta = await asyncio.to_thread(_mb_release_from_recording, recording_id)

    artist = (meta or {}).get("artist") or artist_hint or "Unknown Artist"
    album  = (meta or {}).get("album")  or album_hint  or folder.name
    year   = (meta or {}).get("year")   or ""
    rg_id  = (meta or {}).get("release_group_id", "")

    # Detect hi-res (any file > 16-bit)
    is_hires = _detect_hires(flac_files)
    quality_tag = "FLAC 24bit" if is_hires else "FLAC"

    logger.info("Enrichment metadata: artist=%r album=%r year=%r hires=%s", artist, album, year, is_hires)

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

    art_dir = flac_files[0].parent  # save art alongside the FLAC files
    if cover_bytes:
        (art_dir / "folder.jpg").write_bytes(cover_bytes)
    if cd_bytes:
        (art_dir / "cd.png").write_bytes(cd_bytes)
    if logo_bytes:
        (art_dir / "logo.png").write_bytes(logo_bytes)

    # --- Step 7: Rename the folder ---
    year_part = f" ({year})" if year else ""
    folder_name = _safe_name(f"{artist} - {album}{year_part} [{quality_tag}]")
    renamed = folder.parent / folder_name
    if folder != renamed:
        try:
            folder.rename(renamed)
            folder = renamed
        except Exception as e:
            logger.warning("Folder rename failed: %s", e)

    # --- Step 8: Move to destination ---
    dest = Path(dest_root) / folder.name
    try:
        if dest.exists():
            logger.warning("Destination already exists, skipping move: %s", dest)
        else:
            shutil.move(str(folder), str(dest))
            logger.info("Enrichment delivered: %s", dest)
    except Exception as e:
        logger.error("Enrichment move failed %s → %s: %s", folder, dest, e)
        return False

    # --- Step 9: Navidrome scan ---
    scan_result = await trigger_scan()
    logger.info("Navidrome scan: %s", scan_result)
    return True


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
) -> bool:
    """
    Enrich and deliver a single FLAC track to the Misc/ folder.

    :param flac_path: Absolute path to the FLAC file.
    :param language: "english" | "hindi" | "punjabi"
    :param title_hint: Track title fallback if AcoustID fails.
    :param artist_hint: Artist name fallback if AcoustID fails.
    """
    logger.info("Single-track enrichment started: %s → %s/Misc", flac_path, language)

    file = Path(flac_path)
    if not file.exists():
        logger.error("Track enrichment: file not found: %s", flac_path)
        return False

    dest_root = LANGUAGE_DIRS.get(language.lower(), LANGUAGE_DIRS["english"])

    # --- Step 1+2: Fingerprint → MBID → recording title + artist ---
    recording_id = await _fingerprint_to_mbid(flac_path)
    meta = None
    if recording_id:
        meta = await asyncio.to_thread(_mb_recording_meta, recording_id)

    artist = (meta or {}).get("artist") or artist_hint or "Unknown Artist"
    title  = (meta or {}).get("title")  or title_hint  or file.stem

    logger.info("Track enrichment metadata: artist=%r title=%r", artist, title)

    # --- Step 3: Fetch cover art ---
    cover_bytes = None
    cover_url = await _theaudiodb_track_cover(artist, title)
    if cover_url:
        cover_bytes = await _fetch_bytes(cover_url)

    # --- Step 4: Embed cover into FLAC ---
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
    if language.lower() == "punjabi":
        dest_dir = Path(dest_root)
    else:
        dest_dir = Path(dest_root) / "Misc"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # --- Step 7: Move to destination ---
    dest = dest_dir / file.name
    try:
        if dest.exists():
            logger.warning("Destination already exists, skipping: %s", dest)
        else:
            shutil.move(str(file), str(dest))
            logger.info("Track enrichment delivered: %s", dest)
    except Exception as e:
        logger.error("Track move failed %s → %s: %s", file, dest, e)
        return False

    # --- Step 8: Navidrome scan ---
    scan_result = await trigger_scan()
    logger.info("Navidrome scan after track delivery: %s", scan_result)
    return True


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
