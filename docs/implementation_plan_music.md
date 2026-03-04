# Implementation Plan: Raven Music Maven (Phase 3)
**Version:** 3.4.0  
**Core Service:** `slskd` + `Enrichment Engine` (AcoustID Integration)
**Status:** Ready for Deployment

## 1. Objective
Enable Raven to search, verify, download, and professionally tag lossless (FLAC) music. This ensures every download arrives in your library with high-res art (visible in Foobar2000) and perfect metadata, identified via audio fingerprinting.

## 2. Infrastructure & Networking
- **Docker Image:** `slskd/slskd:latest`
- **Internal Ports:** `5030` (Web UI), `5031` (API).
- **External P2P Port:** `2234` (TCP). 
- **Required Action:** Run `sudo ufw allow 2234/tcp` on the Hostinger VPS.
- **Python Dependencies:** `mutagen` (Tagging), `musicbrainzngs` (Metadata), `pyacoustid` (Fingerprinting).

## 3. Directory Structure (Rclone Mounts)
Raven routes files based on user-selected language categories:
- **English:** `/mnt/cloud/gdrive/Media/Music/English`
- **Hindi:** `/mnt/cloud/gdrive/Media/Music/Hindi`
- **Punjabi:** `/mnt/cloud/gdrive/Media/Music/Punjabi`
- **In-Progress:** `/mnt/cloud/gdrive/Media/Music/Downloads` (Temp slskd path)
- **Staging:** `/mnt/cloud/gdrive/Media/Music/Enrichment_Queue` (Processing area)

## 4. Logical Flow
### Step A: The "Existence" Check
Before searching, Raven recursively scans: English, Hindi, and Punjabi paths.
- **Result:** If the album/song exists, Raven stops to prevent duplicates.

### Step B: The "High-Fidelity" Search
Raven queries `slskd` REST API (`POST /api/v1/searches`) and ranks results:
1. **Tier 1 (Hi-Res):** Files matching `24-bit`, `96kHz`, or `192kHz`.
2. **Tier 2 (Lossless):** Standard `.flac` files.
3. **Filter:** Exclude lossy formats (.mp3, .m4a) by default.

### Step C: Acquisition & Language Routing (Option B)
- Raven presents Top 5 results with Bitrate/Sample Rate labels.
- **User Prompt:** "Which library should this be added to? (1. Eng, 2. Hin, 3. Pun)"
- **Action:** Raven triggers download to the `/Downloads` folder.

### Step D: The Enrichment Pipeline (Metadata & Art)
Once the transfer reaches 100% completion:
1. **Move to Staging:** Transfer files to `/Enrichment_Queue`.
2. **Identify (AcoustID):** Audio fingerprinting to get the MusicBrainz ID (MBID).
3. **Tag (MusicBrainz):** Pull verified Artist, Album, Year, and Tracklist metadata.
4. **Visual Ingest (TheAudioDB + Fanart.tv):**
   - **TheAudioDB:** Fetch primary high-res "Front Cover."
   - **Fanart.tv:** Fetch **CD/Vinyl Disc Art** and **ClearArt logos**.
5. **Embed & Save:** - Embed Front Cover into FLAC headers via `mutagen`.
   - Save `folder.jpg` (Cover), `cd.png` (Disc Art), and `logo.png` (ClearArt) in the album folder.

### Step E: Final Delivery
1. **Move:** Relocate the "Cleaned" folder to the user-selected language root.
2. **Rename:** Enforce standard: `Artist - Album (Year) [FLAC - Quality]`.
3. **Scan:** Trigger a library refresh for Navidrome