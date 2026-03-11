# Sam's Media Assistant (SamAssist)

**Project:** Hybrid AI Media Manager
**Approach:** Custom Media API + Raven (OpenClaw) Integration
**VPS:** sam9scloud.in (IP: 69.62.73.167)
**Deployed path on VPS:** `/root/apps/sam-media-api/`
**Version:** 2.2.0
**Status:** LIVE â€” Deployed, patched, and re-validated end-to-end (2026-02-26)

**Project state note (2026-03-04):**
- The movie/TV/music pipeline is confirmed working end-to-end on the live VPS.
- The Librarian/Kavita subsystem is fully implemented, deployed, and validated end-to-end (2026-03-04).
  - Anna's Archive search + Libgen two-step resolver confirmed working from VPS.
  - EPUB structural validation, Kavita duplicate detection, and post-download scan all confirmed working.
- Current Librarian state is documented in `docs/KAVITA_STATUS_AND_ANNA_PLAN.md`.

**Project state note (2026-03-06):**
- Workflow lock: local repo is code source-of-truth; VPS is runtime test environment.
- YouTube Opus Maven endpoints are implemented and mounted (`/youtube/search`, `/youtube/download`, `/youtube/status/{download_id}`).
- Cookies runtime path activation is complete and validated (host + container visibility, strict invalid-cookie failure path).
- YouTube extraction fix validated: `yt-dlp[default]` + `nodejs` + `--js-runtimes node` resolves challenge path for tested URLs.
- YouTube outputs are language-specific: `Music/{English|Hindi|Punjabi}/YouTube_Music` (not root `Music/YouTube_Music`).
- `/youtube/search` now resolves direct YouTube URLs deterministically as exact result `1`, while keeping the normal confirmation flow.
- `/youtube/status` now exposes actual selected source format details (`source_format_id`, `source_abr_kbps`, `source_acodec`) plus saved path.
- YouTube downloads now run a safe post-download enrichment pass: improve title/artist/album via MusicBrainz when match confidence is high, then resolve art with `Cover Art Archive -> TMDB (exact-year validated when year is present) -> TheAudioDB -> existing embedded thumbnail`.
- YouTube operational detail is documented in `docs/YouTube_Opus Maven.md`.

**Project state note (2026-03-08):**
- Soulseek sharing is implemented and live through `slskd`.
  - Shares: English, Hindi, Punjabi music libraries
  - UI: `https://slsk.sam9scloud.in`
  - Direct raw port `69.62.73.167:5030` is no longer publicly reachable
  - `sam-media-api` now talks to `slskd` via internal Docker service name `http://slskd:5030`
- AzuraCast radio phase 1 and phase 2 are implemented and validated.
  - Live station: `sam9s.radio`
  - Public stream: `https://radio.sam9scloud.in/listen/sam9s.radio/radio.mp3`
  - Canonical radio library: `/mnt/cloud/gdrive/Media/Radio`
  - AzuraCast now reads station media from that shared library path
  - Radio API endpoints live: `GET /radio/nowplaying`, `POST /radio/upload`
  - MP3 uploads now use AzuraCast's native media upload API and are indexed immediately
  - Automated radio uploads currently land in `/mnt/cloud/gdrive/Media/Radio/Hindi`
  - Recursive mixed-folder radio import is implemented and validated through `skills/radio-audio-import/scripts/import_to_radio.py`
    - supported ingest: `.mp3` direct, `.mp4`/`.webm` converted to MP3
    - skipped: `.flac` and unsupported file types
  - Radio phase 3 is intentionally deferred for later planning: DJ drops / liners / rotation rules
  - Detailed handoff/status doc: `docs/RADIO_AZURACAST_STATUS.md`

**Project state note (2026-03-09):**
- Music manual-import mode is implemented inside the existing FLAC music pipeline.
  - New API endpoint: `POST /music/import`
  - Existing status endpoint reused: `GET /music/status/{download_id}`
  - Existing enrichment path reused: AcoustID → MusicBrainz → cover art → FLAC embed → Navidrome scan
  - Duplicate protection added for manual imports:
    - album imports skip when the same album is already present in the target music library
    - single-track imports skip when the same track already exists in the destination
  - Upgrade mode added for manual imports:
    - `replace_existing=true` replaces older raw library copies with the newly enriched import
    - replaced copies are moved to `/mnt/cloud/gdrive/Media/Music/Downloads/manual_import_replaced/`
  - Auto language routing added for manual imports:
    - `language=auto` classifies albums/tracks into `english`, `hindi`, or `punjabi` before delivery
    - intended for the mixed `SoulSeek\\complete` library before the full import run
  - Existing skill updated with manual-import instructions
  - Helper script added: `skills/music/scripts/manual_import_music.py`
- Controlled subset validation passed on:
  - clean album folder
  - synthetic `CD1` / `CD2` multi-disc album folder
  - standalone FLAC track
- Full recursive import of `D:\Softwares_Apps\Entertainment\MUSIC\SoulSeek\complete` was executed in staged form.
  - Main batch imported the clean albums/tracks that could be classified safely.
  - Targeted leftovers pass imported the previously skipped `CD1` / `CD2` / `Disc 1` buckets as single tracks.
  - Current remaining source-vs-library FLAC gap is small and appears to be duplicate suppression/consolidation rather than unhandled leftovers.
  - Repeated album tiles seen in Navidrome are currently a metadata-normalization issue, not an unfinished import issue.
  - Library reorganization/album consolidation is intentionally deferred.
- New orchestration skill added: `skills/media-manager/SKILL.md`
  - Purpose: let Raven act as Sam's top-level media server manager by choosing the correct existing pipeline, preferring targeted operations, verifying outcomes, and avoiding unnecessary reruns.

**Project state note (2026-03-10):**
- Basic Immich photo-management integration is implemented in code.
  - New router: `/photos/*`
  - Supported operations:
    - upload asset
    - list albums
    - create album
    - rename/update album
    - delete album
    - add assets to album
    - search assets by filename
    - update simple asset fields
    - download original asset bytes
  - New skill added: `skills/photos/SKILL.md`
  - Runtime validation is complete.
    - validated: list albums
    - validated: upload asset
    - validated: search asset by filename
    - validated: download original asset bytes
    - validated: create album
    - validated: rename album
    - validated: delete album
  - Current VPS ops issue:
    - `immich-server` is healthy
    - `immich-microservices` is in a restart loop with `/bin/bash: line 1: start-microservices: command not found`
    - this looks like a deployment command/config issue rather than an application-data issue

**Project state note (2026-03-10, Recommendation Engine Phase 1):**
- Recommendation engine phase 1 is now implemented inside `sam-media-api`.
  - New router: `/recommendations/*`
  - Storage: existing `media-assistant-postgres` in schema `reco`
  - Ingestion sources: Jellyfin, Navidrome, Kavita
  - Source truth access: direct read-only SQLite mounts from the service databases
  - Phase 1 behavior: suggest-only, weekly + on-demand, no auto-downloads
  - New skill added: `skills/recommendations/SKILL.md`
  - New helper scripts added for host scheduling:
    - `scripts/recommendation_ingest.py`
    - `scripts/recommendation_generate.py`
    - `scripts/recommendation_ingest.sh`
    - `scripts/recommendation_generate.sh`
  - LLM theme synthesis is optional and only activates when `OPENROUTER_API_KEY` is configured
  - External candidate enrichment is optional and currently uses:
    - TMDB for movies / screen titles
    - Last.fm for music
    - Open Library for books
  - Telegram delivery is still deferred until OpenClaw is deployed on VPS
  - VPS cron is now installed with `CRON_TZ=Asia/Kolkata`:
    - daily ingest at `03:30`
    - weekly digest generation every Sunday at `09:00`

**Project state note (2026-03-10, OpenClaw VPS Baseline):**
- Old OpenClaw runtime residue on the VPS has been cleaned and backed up.
  - Backup archive kept at `/root/backups/openclaw-backup-20260310-122058.tgz`
  - Removed: old `~/.openclaw` runtime state, old OpenClaw PM2 processes, old global npm install, old shell completion hook, and stale user/systemd fragments tied to the previous deployment
- Fresh OpenClaw baseline is now installed on the VPS.
  - Version: `OpenClaw 2026.3.8`
  - Runtime: Node `v22.22.0`
  - Gateway mode: loopback-only
  - Gateway service: systemd user service, healthy
  - OpenRouter auth is configured and working
- Raven workspace is now wired into the fresh OpenClaw instance.
  - Workspace skills copied from `skills/` into `~/.openclaw/workspace/skills/`
  - Identity/routing files copied from `openclaw/` into `~/.openclaw/workspace/`
  - Ready custom skills:
    - `media-assistant`
    - `media-manager`
    - `music`
    - `youtube`
    - `librarian`
    - `photos`
    - `radio`
    - `radio-audio-import`
    - `recommendations`
    - `vps-health`
- Model baseline is configured explicitly instead of relying on generic OpenRouter auto-routing.
  - default: `reasoning -> openrouter/google/gemini-2.5-pro`
  - alias: `cheap -> openrouter/moonshotai/kimi-k2.5`
  - alias: `coding -> openrouter/openai/gpt-5-codex`
- Local agent-turn validation has passed against the live workspace and skill set.
- Telegram channel setup is intentionally still pending.
  - Next required input: fresh Telegram bot token for the clean Raven deployment
  - Recommendation: rotate any previously used bot/gateway tokens rather than reusing the old OpenClaw-era secrets

**Project state note (2026-03-11, proactive completion notifications):**
- `sam-media-api` now supports Telegram completion notifications for successful pipeline events using Raven's bot token/chat.
- Current wiring:
  - movie/TV complete webhook -> best-effort Jellyfin availability check -> notify Sam
  - librarian download -> best-effort Kavita availability check -> notify Sam
  - music / YouTube import success -> notify Sam after successful delivery with Navidrome scan context
  - radio upload success -> notify Sam
  - photos upload success -> notify Sam
- This is event-driven, not timer-based.
- Raven does not need to sleep/poll manually for these completions anymore.

**Project state note (2026-03-11, unified progress layer):**
- `sam-media-api` now exposes a lightweight unified jobs API:
  - `GET /jobs`
  - `GET /jobs/{job_id}`
- Current real progress coverage:
  - music/Soulseek: bytes, percent, speed, ETA, file counts
  - YouTube: percent, bytes, speed, ETA when yt-dlp emits progress
- Existing per-pipeline status endpoints also expose richer progress fields for music and YouTube.
- Books/photos/radio remain coarse success-state pipelines because fine-grained progress adds little value there.

**Project state note (2026-03-11, persistent jobs + failure visibility):**
- Music and YouTube jobs now persist in Postgres-backed job storage instead of memory only.
- `GET /jobs/{job_id}` survives `sam-media-api` restarts.
- `GET /music/status/{download_id}` and `GET /youtube/status/{download_id}` now fall back to persisted job records when the in-memory entry is gone.
- Music and YouTube terminal failures now store the exact reason durably.
- Failure notifications are now sent for music/YouTube terminal failure cases as well as success cases.

**Project state note (2026-03-11, startup-check notifications for music + YouTube):**
- Music and YouTube now send one bounded early follow-up notification shortly after a job is launched.
- That early message tells Sam one of three things:
  - the download has started and real progress is visible
  - the job is queued but no progress is visible yet
  - the job failed quickly with the exact reason
- Existing terminal success/failure notifications remain in place.
- This is intentionally limited to one startup-check message plus one terminal message to avoid spam.

---

## 1. WHAT IS BUILT

A lean FastAPI service (`sam-media-api`) that gives Raven (the AI assistant) full control over:
- Searching torrent trackers (Jackett + iptorrents) for movies, TV, music
- Adding torrents to qBittorrent with correct save paths and title tags
- Receiving a webhook from qBittorrent on download completion â†’ copying with clean destination naming to Jellyfin library / Google Drive
- Triggering Jellyfin library refresh so content appears immediately

**What was NOT built (future phases):**
- Audiobookshelf book search / ingestion
- Recommendation engine phase 2+ (vector search, Audiobookshelf ingestion, automatic Telegram delivery, auto-acquisition)

**What has since been added beyond the original scope:**
- Full Librarian/Kavita pipeline: search (SE + Gutenberg + Archive.org + Anna's Archive), EPUB download, structural validation, Kavita scan — confirmed end-to-end (2026-03-04)
- Anna's Archive: HTML scraper + Libgen two-step resolver (ads.php → get.php); no unofficial wrappers, httpx + BeautifulSoup only
- EPUB structural validation (ZIP → container.xml → OPF → dc:title) rejects malformed files before Kavita ingestion
- YouTube Opus Maven router (`/youtube/*`) with playlist-first search, background download worker, and Navidrome scan hook
- Radio router (`/radio/*`) for AzuraCast now-playing and MP3 radio-library upload
- Radio batch importer skill/script for recursive mixed-folder ingest into AzuraCast
- Music manual-import helper script for local FLAC file/folder staging to VPS + pipeline trigger
- Basic Immich photo-management router/skill for upload, albums, search, and download
- Recommendation router (`/recommendations/*`) for ingestion, weekly digest generation, on-demand queries, and digest history

---

## 2. ACTUAL SYSTEM ARCHITECTURE

```
Raven (OpenClaw)
    â”‚
    â–¼ HTTP + X-API-Key header
sam-media-api  (port 8765 on VPS host, :8000 inside container)
    â”‚
    â”œâ”€â”€ /search  â”€â”€â–¶  Jackett (internal Docker: jackett:9117)
    â”‚              â”€â”€â–¶  iptorrents.com RSS (external)
    â”‚              â”€â”€â–¶  TMDB API (external metadata)
    â”‚
    â”œâ”€â”€ /download â”€â”€â–¶  qBittorrent API (https://downloads.sam9scloud.in)
    â”‚                  saves to /downloads/complete/{category}/
    â”‚                  stores "Title|Year" as torrent tag
    â”‚
    â”œâ”€â”€ /status  â”€â”€â–¶  qBittorrent active downloads
    â”‚              â”€â”€â–¶  Jellyfin search (optional title check)
    â”‚
    â””â”€â”€ /complete  â—€â”€â”€ qBittorrent fires this on torrent completion
                       1. Reads "Title|Year" tag from qBT
                       2. Leaves source file/folder name untouched (preserves seeding integrity)
                       3. Copies to /mnt/cloud/gdrive/Media/{category}/ using clean destination name
                          (= Google Drive FUSE mount = Jellyfin library simultaneously)
                       4. Jellyfin refresh â†’ appears in library immediately
```

### Key infrastructure insight
`/mnt/cloud/gdrive/Media` on the VPS host is a **live rclone FUSE mount** of Google Drive.
Jellyfin mounts this same path as `/media` inside its container.
**One `shutil.copy2` call serves both Google Drive archive AND Jellyfin library** â€” no separate rclone subprocess needed.

---

## 3. ACTUAL FILE STRUCTURE (local dev at D:\RAVENs\media_assistant\)

```
media_assistant/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI — all endpoints, SAVE_PATHS, MEDIA_PATHS, /complete logic
│   ├── config.py        # Pydantic Settings — reads .env (incl. Kavita + AA vars)
│   ├── jackett.py       # Jackett search client
│   ├── iptorrents.py    # iptorrents RSS parser
│   ├── tmdb.py          # TMDB metadata client
│   ├── qbittorrent.py   # qBittorrent API client (add torrent, get tags, active downloads)
│   ├── jellyfin.py      # Jellyfin API client (search, refresh_library)
│   ├── immich.py        # Immich API client
│   ├── kavita.py        # Kavita API client (login, search, get_library_id, scan_library)
│   ├── librarian.py     # Librarian router — book search, download, EPUB validation, Kavita scan
│   ├── photos.py        # Photos router — Immich upload, albums, search, download
│   ├── radio.py         # Radio router — AzuraCast now-playing + MP3 upload to radio library
│   ├── recommendation_db.py      # Postgres schema + persistence for recommendation engine
│   ├── recommendation_engine.py  # ingestion, scoring, digest generation, query logic
│   ├── recommendation_sources.py # direct DB readers for Jellyfin/Navidrome/Kavita
│   ├── recommendations.py        # Recommendation router — /recommendations/*
│   └── sources/
│       ├── gutendex.py          # Gutenberg/Gutendex search client
│       ├── standard_ebooks.py   # Standard Ebooks search client
│       ├── archive_org.py       # Archive.org search client
│       └── annas_archive.py     # Anna's Archive scraper + Libgen two-step resolver
├── skills/
│   ├── media-assistant/
│   │   └── SKILL.md     # Raven skill — movie/TV/music torrent pipeline
│   ├── media-manager/
│   │   └── SKILL.md     # Raven orchestration skill — top-level media server manager
│   ├── photos/
│   │   └── SKILL.md     # Raven skill — basic Immich photo management
│   ├── recommendations/
│   │   └── SKILL.md     # Raven skill — weekly digest and on-demand discovery
│   ├── radio/
│   │   └── SKILL.md     # Raven skill — AzuraCast radio upload + now-playing
│   ├── radio-audio-import/
│   │   ├── SKILL.md     # Raven skill — recursive mixed-folder import into radio
│   │   └── scripts/
│   │       └── import_to_radio.py
│   └── librarian/
│       └── SKILL.md     # Raven skill — book search, download, Kavita
├── docs/
│   ├── PROJECT_PLAN.md              ← THIS FILE
│   ├── KAVITA_STATUS_AND_ANNA_PLAN.md  # Librarian current state
│   ├── RADIO_AZURACAST_STATUS.md    # Radio current state and migration notes
│   ├── INFRASTRUCTURE_AUDIT.md
│   └── DR_RUNBOOK.md                # Backup + restore procedure
├── scripts/
│   └── backup_config_bundle.sh      # Encrypted config-only backup script
│   ├── recommendation_ingest.py     # Host-scheduler helper — trigger daily ingestion
│   └── recommendation_generate.py   # Host-scheduler helper — trigger weekly generation
├── docker-compose.yml   # sam-media-api + jackett + flaresolverr containers
├── Dockerfile
├── requirements.txt
└── .env                 # secrets — never commit
```
---

## 4. IMPLEMENTED ENDPOINTS

### `GET /health`
No auth. Returns `{"status": "ok"}`. Used to verify container is up.

### `POST /search`
**Auth required.** Searches Jackett + iptorrents in parallel, enriches with TMDB metadata.

```json
Request:
{
  "query": "Sinners 2025",
  "quality": "1080p",        // optional â€” filters results containing this string
  "limit": 5,                // results per source (default 5)
  "min_size_gb": 10.0,       // optional size filter
  "max_size_gb": 20.0        // optional size filter
}

Response:
{
  "query": "Sinners 2025",
  "metadata": { "poster_url": "...", "rating": 7.8, "imdb_url": "...", "overview": "...", "year": 2025 },
  "results": [
    { "index": 1, "title": "...", "size": "14.2 GB", "seeders": 120, "torrent_url": "...", "source": "PrivateHD" },
    ...
  ],
  "total_found": 8,
  "sources": { "PrivateHD": 3, "iptorrents": 5 }
}
```

### `POST /download`
**Auth required.** Sends `.torrent` file bytes to qBittorrent with correct save path and tags.

```json
Request:
{
  "torrent_url": "https://...",
  "category": "hollywood",     // see categories below
  "title": "Sinners",
  "year": 2025                 // stored as qBT tag "Sinners|2025" for rename at completion
}
```

### `GET /status`
**Auth required.** Returns active qBittorrent downloads. Optional `?title=X` checks Jellyfin library.

### `POST /complete`
**Auth required.** Called by qBittorrent on torrent completion. NOT called manually.
- Reads title+year from qBT tag
- Keeps qB source file/folder names unchanged (for stable post-restart seeding)
- Copies to Google Drive FUSE mount (= Jellyfin library) as clean destination name `Title (Year).*`
- Fires Jellyfin library refresh

---

## 5. CATEGORY MAPPING

| Media type | Category key | qBT save path (container) | Google Drive / Jellyfin path |
|---|---|---|---|
| Hollywood movies | `hollywood` | `/downloads/complete/Movies/Hollywood` | `/mnt/cloud/gdrive/Media/Movies/Hollywood` |
| Hindi movies | `hindi` | `/downloads/complete/Movies/Hindi` | `/mnt/cloud/gdrive/Media/Movies/Hindi` |
| TV (English/Western) | `tv-hollywood` | `/downloads/complete/TV/Hollywood` | `/mnt/cloud/gdrive/Media/TV/Hollywood` |
| TV (Hindi/Indian) | `tv-indian` | `/downloads/complete/TV/Indian` | `/mnt/cloud/gdrive/Media/TV/Indian` |
| Music (English) | `music-english` | `/downloads/complete/Music/English` | `/mnt/cloud/gdrive/Media/Music/English` |
| Music (Hindi) | `music-hindi` | `/downloads/complete/Music/Hindi` | `/mnt/cloud/gdrive/Media/Music/Hindi` |
| Music (Punjabi) | `music-punjabi` | `/downloads/complete/Music/Punjabi` | `/mnt/cloud/gdrive/Media/Music/Punjabi` |

---

## 6. VPS PATH MAP (critical â€” do not confuse these)

| What | VPS host path | Container-internal path | Notes |
|---|---|---|---|
| qBittorrent downloads | `/srv/downloads` | `/downloads` (in qBT + our container) | Both containers mount this |
| Completed downloads | `/srv/downloads/complete/Movies/Hollywood/` | `/downloads/complete/Movies/Hollywood/` | qB source files stay untouched for seeding |
| Google Drive FUSE | `/mnt/cloud/gdrive` | `/mnt/cloud/gdrive` (same) | rclone FUSE, allow_other |
| Jellyfin library | `/mnt/cloud/gdrive/Media/` | `/media/` (inside Jellyfin container) | One write = gdrive + Jellyfin |
| qBT watch folder | `/srv/torrents/watch` | `/watch` (in qBT container) | Manual .torrent drops |
| Our API source | `/root/apps/sam-media-api/` | â€” | Deployed here on VPS |

---

## 7. DOCKER COMPOSE (current production state)

```yaml
services:
  sam-media-api:
    build: .
    container_name: sam-media-api
    restart: unless-stopped
    ports:
      - "8765:8000"   # 0.0.0.0 binding â€” qBittorrent can call 172.17.0.1:8765
    env_file:
      - .env
    volumes:
      - /srv/downloads:/downloads                         # shared with qBittorrent
      - /mnt/cloud/gdrive/Media:/mnt/cloud/gdrive/Media  # rclone FUSE = gdrive = Jellyfin
    dns:
      - 8.8.8.8
      - 8.8.4.4
    depends_on:
      - jackett
    networks:
      - internal

  jackett:
    image: lscr.io/linuxserver/jackett:latest
    container_name: jackett
    restart: unless-stopped
    ports:
      - "127.0.0.1:9117:9117"   # UI on localhost only
    volumes:
      - jackett_config:/config
    dns:
      - 8.8.8.8
      - 8.8.4.4
    depends_on:
      - flaresolverr
    networks:
      - internal

  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr:latest
    container_name: flaresolverr
    restart: unless-stopped
    environment:
      - LOG_LEVEL=info
    networks:
      - internal

networks:
  internal:
    driver: bridge

volumes:
  jackett_config:
```

**Why `8765:8000` not `127.0.0.1:8765:8000`:**
qBittorrent's "Run on completion" executes inside its Docker container and calls our API via
`172.17.0.1:8765` (Docker bridge gateway = host IP). The `127.0.0.1` binding would block this.

---

## 8. ENVIRONMENT VARIABLES (.env â€” never commit)

```bash
# qBittorrent
QB_USERNAME=admin
QB_PASSWORD=<password>
QB_URL=https://downloads.sam9scloud.in   # default in config.py

# Jackett
JACKETT_URL=http://jackett:9117          # internal Docker service name
JACKETT_API_KEY=<key>

# iptorrents
IPTORRENTS_RSS_BASE_URL=https://iptorrents.com/t.rss?u=...;tp=...;download;s0=10

# API Security
API_KEY=<strong random string>

# Jellyfin
JELLYFIN_URL=https://movies.sam9scloud.in
JELLYFIN_API_KEY=<key>

# TMDB
TMDB_API_KEY=<key>

# Config backup encryption
BACKUP_PASSPHRASE=<strong backup passphrase>

# Kavita
KAVITA_URL=http://172.17.0.1:8091   # Docker bridge host IP (NOT localhost)
KAVITA_USERNAME=<username>
KAVITA_PASSWORD=<password>

# Anna's Archive (optional — enables slow_download fallback if Libgen fails)
ANNA_ARCHIVE_COOKIE=
```

---

## 9. WHAT WAS TESTED AND CONFIRMED WORKING (2026-02-22, 2026-02-26)

| Test | Result |
|---|---|
| `GET /health` returns `{"status":"ok"}` | PASS |
| `POST /complete` with dummy 1MB `.mkv` file | PASS |
| Destination named cleanly: `Test Movie (2024).mkv` | PASS |
| File copied to `/mnt/cloud/gdrive/Media/Movies/Hollywood/` | PASS |
| Jellyfin `refresh_library()` fired | PASS |
| `/complete` keeps source path unchanged and renames destination only | PASS |
| qB restart after completion retains seeding for newly completed torrent | PASS |
| qB upload queue issue fixed (`queueing_enabled=false`, active limits set to 20) | PASS |
| Cleanup of test files | Done |
| `POST /librarian/search` returns AA + other results | PASS (2026-03-04) |
| Anna's Archive Libgen resolver returns direct download URL | PASS (2026-03-04) |
| EPUB structural validation passes for good EPUB | PASS (2026-03-04) |
| `scan_triggered: true` after download | PASS (2026-03-04) |
| Book appears in Kavita after scan | PASS (2026-03-04) |
| `already_in_kavita: true` for book already in library | PASS (2026-03-04) |
| `already_in_kavita: false` for book not in library | PASS (2026-03-04) |
| `POST /youtube/search` returns non-empty results with cookies file loaded | PASS (2026-03-06) |
| Invalid/empty `youtube_cookies.txt` fails fast with explicit cookie error | PASS (2026-03-06) |
| `POST /youtube/download` completes media extraction on VPS | PASS (2026-03-06) |

**Test command used:**
```bash
# On VPS
curl -s -X POST http://localhost:8765/complete \
  -H "X-API-Key: <key>" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test Movie|2024","category":"hollywood",
       "content_path":"/downloads/complete/Movies/Hollywood/Test.Movie.2024.1080p.BluRay.mkv",
       "info_hash":"abc123"}'
# Response: {"renamed": ["Test Movie (2024).mkv"], "jellyfin_refreshed": true}
```

---

## 10. qBITTORRENT SETTINGS (CURRENT PRODUCTION STATE)

These settings are configured in production (`downloads.sam9scloud.in`) and stored in:
`/opt/dokploy/volumes/qbittorrent/config/qBittorrent/qBittorrent.conf`

### A. "Run on completion" webhook (enabled)
Settings â†’ Downloads â†’ **Run External Program on torrent completion**
```
curl -s -X POST http://172.17.0.1:8765/complete -H "X-API-Key: <API_KEY>" -H "Content-Type: application/json" -d "{\"name\":\"%N\",\"category\":\"%L\",\"content_path\":\"%F\",\"info_hash\":\"%I\"}"
```
- `172.17.0.1` = Docker bridge gateway (host IP as seen from inside qBT container)
- `%N` = torrent name, `%L` = category label, `%F` = file/folder path, `%I` = info hash
- Fires **after download completes**, not when added
- qB config block: `[AutoRun] enabled=true`

### B. 30-day seeding auto-delete (enabled)
Settings â†’ BitTorrent â†’ Seeding Limits:
- Enable: When seeding time reaches **43200 minutes** (30 days)
- Action: **Remove torrent and delete data**

**Effect:** qBittorrent keeps seeding for 30 days, then deletes its local copy from `/srv/downloads/complete/`.
Google Drive and Jellyfin keep the file permanently (copied at completion time).

### C. Queueing limits (updated)
- `Session\QueueingSystemEnabled=false` (seed all completed torrents; avoid `queuedUP` cap)
- `Session\MaxActiveTorrents=20`
- `Session\MaxActiveDownloads=20`
- `Session\MaxActiveUploads=20`

---

## 11. DEPLOYMENT PROCEDURE (how to redeploy if needed)

```bash
# From local Windows machine
scp -i ~/.ssh/id_rsa -r app docker-compose.yml Dockerfile requirements.txt .env \
  root@69.62.73.167:/root/apps/sam-media-api/

# On VPS
ssh -i ~/.ssh/id_rsa root@69.62.73.167
cd /root/apps/sam-media-api
docker compose build sam-media-api
docker compose up -d --force-recreate

# Verify
curl http://localhost:8765/health
# â†’ {"status":"ok"}
```

### Sync model (locked)
- Code edits happen in local repo first: `D:\RAVENs\media_assistant`
- Deploy those code changes to VPS for live runtime testing
- Push to GitHub only after VPS validation passes
- Runtime secrets/files remain VPS-only and untracked (`.env`, `youtube_cookies.txt`, service state)

---

## 11a. LIBRARIAN ENDPOINTS

All routes under `/librarian/`, mounted from `app/librarian.py`.

### `GET /librarian/health`
No auth. Returns `{"status": "ok", "service": "librarian"}`.

### `POST /librarian/search`
**Auth required.** Searches Standard Ebooks + Gutenberg + Archive.org + Anna's Archive in parallel.

```json
Request:  { "query": "Atomic Habits James Clear", "limit": 5 }

Response:
{
  "query": "...",
  "already_in_kavita": false,
  "results": [
    {
      "index": 1, "title": "...", "author": "...", "year": 2018,
      "format": "epub", "size_mb": 0.6,
      "source": "AnnasArchive",
      "source_id": "/md5/abc123",       // present for AnnasArchive results
      "download_url": "https://annas-archive.gl/md5/abc123",  // clickable detail page
      "cover_url": null
    }
  ],
  "total_found": 8,
  "sources": { "Standard Ebooks": 2, "AnnasArchive": 5, "Archive.org": 1 }
}
```

### `POST /librarian/download`
**Auth required.** Download and save to Kavita library.

```json
// For standard sources (SE / Gutenberg / Archive.org):
{ "download_url": "https://...", "title": "...", "author": "...", "category": "novel", "format": "epub" }

// For Anna's Archive results:
{ "source": "AnnasArchive", "source_id": "/md5/abc123", "title": "...", "author": "...", "category": "novel", "format": "epub" }
```

Categories: `novel` | `comic` | `magazine`

Save paths:
- novel    → `/mnt/cloud/gdrive/Media/Books/{Author}/{Title}.epub`
- comic    → `/mnt/cloud/gdrive/Media/Comics/{Author}/{Title}.epub`
- magazine → `/mnt/cloud/gdrive/Media/Magazines/{Author}/{Title}.epub`

Response: `{ "success": true, "saved_to": "...", "size_mb": 0.55, "kavita_safe": true, "scan_triggered": true, "scan_error": null, "already_existed": false }`

### `GET /librarian/status?title=X`
**Auth required.** Check if title is in Kavita library.

### `POST /librarian/scan`
**Auth required.** Manually trigger Kavita scan: `{ "category": "novel" }`

---

## 11b. RADIO ENDPOINTS

All routes under `/radio/`, mounted from `app/radio.py`.

### `GET /radio/nowplaying`
**Auth required.** Proxies the public AzuraCast now-playing feed through our API and returns the configured station row.

Response:
```json
{
  "status": "ok",
  "station": {
    "id": 2,
    "name": "sam9s.radio",
    "shortcode": "sam9s.radio",
    "listen_url": "https://radio.sam9scloud.in/listen/sam9s.radio/radio.mp3"
  },
  "listeners": { "total": 0, "unique": 0, "current": 0 },
  "now_playing": {
    "title": "...",
    "artist": "...",
    "album": "...",
    "art": "...",
    "played_at": 1772954896,
    "duration": 345,
    "playlist": "sam9sradio",
    "is_request": false
  }
}
```

### `POST /radio/upload`
**Auth required.** Accepts one MP3 file upload and saves it into the shared radio library folder.

- Destination path: `/mnt/cloud/gdrive/Media/Radio/Hindi`
- Supported format in current phase: `.mp3` only
- Duplicate handling: returns `409` unless `replace=true`

Current behavior:
- uploads MP3 directly into AzuraCast station media using the authenticated native API
- verifies the file is indexed immediately after upload

### Radio batch import workflow
For mixed local media folders, use:

`skills/radio-audio-import/scripts/import_to_radio.py`

Current validated behavior:
- recursively scans a source directory
- uploads `.mp3` directly
- converts `.mp4` and `.webm` to temporary MP3 with `ffmpeg`
- skips `.flac`
- uploads everything through `POST /radio/upload`
- validated successfully on `D:\Softwares_Apps\Entertainment\MUSIC`

### `POST /music/import`
**Auth required.** Imports a FLAC file or folder that already exists on the VPS filesystem.

```json
{
  "source_path": "/mnt/cloud/gdrive/Media/Music/Downloads/manual_imports/<id>/<source>",
  "language": "hindi",
  "mode": "auto"
}
```

Behavior:
- `mode=auto` resolves to album for multi-file folders and track for a single FLAC
- album imports reuse the existing album enrichment/delivery path
- track imports reuse the existing single-track enrichment/delivery path
- status is reported through `GET /music/status/{download_id}`

### Music manual-import workflow
For local FLAC files or folders that already exist on Sam's machine, use:

`skills/music/scripts/manual_import_music.py`

Current validated behavior:
- stages a local FLAC file or folder to VPS-visible import storage
- calls `POST /music/import`
- waits on `GET /music/status/{download_id}` when requested
- skips duplicate albums/tracks instead of creating second library copies
- validated successfully on a controlled subset:
  - clean album folder
  - synthetic multi-disc `CD1` / `CD2` folder
  - standalone FLAC track

---

## 12. RAVEN SKILL

Located at: `skills/media-assistant/SKILL.md`

Raven uses these categories when calling `/download`:
- Hollywood, Hindi movies: `hollywood` / `hindi`
- TV shows (English): `tv-hollywood`
- TV shows (Hindi/Indian): `tv-indian`
- Music: `music-english` / `music-hindi` / `music-punjabi`

When Raven doesn't know the category, it asks: "Hollywood, Hindi, TV-Hollywood, or TV-Indian?"

---

## 13. KNOWN INFRASTRUCTURE NOTES

- **qb-shim** (port 8088): Exists only for Grafana dashboard. Has nothing to do with this project.
- **Old media-assistant project**: `/root/apps/media-assistant/` â€” older, heavier version with Postgres+Redis. Our project is the lean replacement at `/root/apps/sam-media-api/`.
- **rclone**: Already installed at `/usr/bin/rclone`. `gdrive:` remote already configured. FUSE-mounted at `/mnt/cloud/gdrive` with `allow_other` so Docker containers can write to it.
- **Jackett**: Running inside the same Docker Compose stack, reachable as `http://jackett:9117` from `sam-media-api`.
- **Config backup implemented**: `scripts/backup_config_bundle.sh` creates encrypted config-only backups.
- **Backup destination**: `/mnt/cloud/gdrive/Backups/sam-media-assistant-config/`

---

## 14. DISASTER RECOVERY

- Full DR instructions: `docs/DR_RUNBOOK.md`
- Backup command:
  `BACKUP_PASSPHRASE='<passphrase>' ./scripts/backup_config_bundle.sh`
- Backup scope: config + secrets + service state (no `/srv/downloads` media payload)
- Output per backup:
  encrypted archive (`.tar.gz.enc`) + checksum (`.sha256`) + metadata (`.metadata.txt`)

---

## 15. FUTURE PHASES (not yet built)

- **Immich photo search** â€” `GET /photos/search`
- **AzuraCast radio phase 3** - DJ drops / liners / rotation rules
- **Librarian improvements** - duplicate detection edge cases (title metadata mismatch), subtitle attachment to downloaded books
- **Audiobookshelf** - audiobook search / ingestion
- **Recommendation engine** â€” "suggest something like Blade Runner"
- **Dashboard widget** â€” embedded chat UI in RamenUI

