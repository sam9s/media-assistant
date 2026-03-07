# YouTube Opus Maven — Operational Status
**Version:** 5.2.0  
**Last validated:** 2026-03-06  
**State:** Implemented and validated end-to-end on VPS.

## 1. Objective
Provide Raven/OpenClaw endpoints to:
- search YouTube (and optional configured playlists first)
- queue a selected result for background audio extraction with `yt-dlp`
- deliver output to language-specific `YouTube_Music` folders
- trigger Navidrome scan after successful download
- run a safe post-download metadata enrichment pass on saved `.opus` files

## 2. Current Implementation (Code Truth)
Implemented router: `app/youtube.py`  
Mounted in: `app/main.py`

Endpoints:
- `POST /youtube/search`
- `POST /youtube/download`
- `GET /youtube/status/{download_id}`

Current search behavior:
- Text query -> playlist-first search, then general YouTube search
- Direct YouTube URL in `query` -> exact video resolution, returned as result `1` for confirmation

Current destination paths:
- English: `/mnt/cloud/gdrive/Media/Music/English/YouTube_Music`
- Hindi: `/mnt/cloud/gdrive/Media/Music/Hindi/YouTube_Music`
- Punjabi: `/mnt/cloud/gdrive/Media/Music/Punjabi/YouTube_Music`

Important:
- The root folder `/mnt/cloud/gdrive/Media/Music/YouTube_Music` is not used for final files.
- Always verify downloads inside language-specific folders listed above.

Download command path uses:
- `yt-dlp` subprocess
- Opus-first format chain:
  - `bestaudio[format_id=774]`
  - fallback `bestaudio[acodec=opus]`
  - fallback `bestaudio[ext=webm]`
  - fallback `bestaudio`

Post-download enrichment path:
- Uses MusicBrainz search to improve title / artist / album when the match is confident
- Tries TheAudioDB for better cover art
- Preserves existing embedded YouTube thumbnail when no authoritative art is found
- Does not rename or move the file again after download

## 3. Cookies Runtime Model (Mandatory)
Cookies file path used by app config:
- host: `/root/apps/sam-media-api/youtube_cookies.txt`
- container: `/app/youtube_cookies.txt`

Mount is intentionally writable (not `:ro`) because `yt-dlp` updates/saves cookie jar state.

Validation enforced in code:
- file must exist
- file must be non-empty
- first line must include Netscape cookie header

If invalid, `/youtube/download` fails immediately with:
`YouTube cookies not configured. Export youtube_cookies.txt from Chrome (YouTube Premium) and upload to the VPS. See setup instructions.`

Behavior contract:
- Valid cookies present -> premium path available.
- Missing/invalid cookies -> immediate explicit failure.
- No silent quality downgrade.

## 4. 2026-03-06 Validation Results
### Passed
1. Cookie activation
- local cookie file exported and normalized to `youtube_cookies.txt`
- copied to VPS runtime path
- host/container byte size matched
- Netscape header and `.youtube.com` entries confirmed

2. Search endpoint
- `POST /youtube/search` returned non-empty results with valid `search_id`

3. Negative-path behavior
- cookie file truncated to 0 bytes (temporary test)
- `/youtube/download` failed immediately with explicit cookie error
- cookie file restored after test

4. End-to-end download (same user-provided URL)
- query/url: `https://youtu.be/aGsuAtj0R1g?si=xkrxZs7RKRYReV_R`
- `POST /youtube/search` returned exact match
- `POST /youtube/download` reached `status: done`
- output saved at:
  - `/mnt/cloud/gdrive/Media/Music/Hindi/YouTube_Music/Same Banna - Topic - Kitni Bachain Hu ME Yar Se Milne Ke Liye.opus`
- ffprobe confirmed:
  - audio codec: `opus`
  - metadata tags present (`title`, `artist`, `album`, etc.)
  - embedded cover art present (`attached_pic=1`)

### Passed (2026-03-07)
5. Direct URL deterministic path
- `POST /youtube/search` with a raw YouTube URL returned the exact video as result `1`
- `/youtube/download` still used the standard `search_id + result_index` flow after confirmation

6. Download diagnostics in status
- `GET /youtube/status/{download_id}` now reports:
  - `source_format_id`
  - `source_abr_kbps`
  - `source_acodec`
  - `saved_to`
  - `output_codec`
  - `output_sample_rate`
  - `output_bitrate_kbps` when ffprobe exposes it

7. Post-download metadata enrichment
- `GET /youtube/status/{download_id}` now also reports:
  - `enrichment_status`
  - `enrichment_source`
  - `enriched_title`
  - `enriched_artist`
  - `enriched_album`
  - `cover_art_applied`
  - `cover_art_source`
- validated on:
  - `https://youtu.be/wv3gUO9Eo0E?si=RrpUwzIRGdWhDBeY`
- observed result:
  - source format: `251` / `126.1 kbps` Opus
  - enriched tags: title `Justuju Jiski Hai`, artist `Asha Bhosle`, album `Umrao Jaan`
  - cover art source remained `existing-embedded` because no stronger authoritative art was returned by the current metadata sources

## 5. Runtime Changes Applied in This Pass
1. `docker-compose.yml`
- changed cookies mount from read-only to writable:
  - from `./youtube_cookies.txt:/app/youtube_cookies.txt:ro`
  - to   `./youtube_cookies.txt:/app/youtube_cookies.txt`

2. `Dockerfile`
- added `nodejs` package to support yt-dlp JS runtime needs

3. `requirements.txt`
- upgraded yt-dlp dependency to include EJS components:
  - `yt-dlp[default]>=2024.1.0`
- this installs `yt-dlp-ejs` for challenge solving support

4. `app/youtube.py`
- added strict cookies validation helper
- download now fails fast for missing/empty/invalid cookie file
- base yt-dlp Python options include cookiefile only when valid
- yt-dlp subprocess now forces JS runtime:
  - `--js-runtimes node`

## 6. Operational Cookie Lifecycle
Typical process:
1. Export `cookies.txt` from Chrome while logged into YouTube Premium
2. Save/upload as `youtube_cookies.txt` to `/root/apps/sam-media-api/`
3. No code changes needed

Expected rotation:
- re-export every few months or when YouTube session expires

## 7. Current Next Technical Step
No critical blocker remains for YouTube Opus Maven.

Recommended hardening:
1. Weekly smoke test with one known URL and one fresh search query
2. Re-export cookies when YouTube starts returning auth/challenge errors
