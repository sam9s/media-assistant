# Raven Media Server: Master Roadmap (March 2026)
**Owner:** Sam9s | **System:** Raven Automation Engine

## Phase 1-3: Completed and Stable
- Movies/TV: automated via qBittorrent
- E-books: Anna's Archive + Kavita pipeline working end-to-end
- Music (FLAC): Soulseek (slskd) pipeline working end-to-end with metadata enrichment

---

## Phase 3.5: Music Sharing (Implemented)
- Goal: contribute back to P2P by sharing libraries
- Implemented:
  - read-only `English`, `Hindi`, `Punjabi` mounts added to `slskd`
  - `slskd` share scan validated
  - HTTPS web UI added at `https://slsk.sam9scloud.in`
  - raw public `:5030` access closed; direct IP access no longer works

---

## Phase 4: AzuraCast Radio (Current Focus)
- Goal: make Raven Radio manageable from chat
- Phase 1:
  - migrate station media to `/mnt/cloud/gdrive/Media/Radio` - implemented
- Phase 2:
  - upload MP3 into radio via native AzuraCast media API - implemented
  - expose `GET /radio/nowplaying` - implemented
- Phase 3:
  - DJ drops / liners / rotation rules

## Phase 5: Audiobook Maven
- Goal: automate audiobook acquisition and podcast management
- Task: connect Raven to Audiobookshelf API
- Logic: source audiobook-compatible formats, ingest, trigger Audiobookshelf scans

---

## Phase 6: YouTube/Opus Maven (Implemented and Validated)
- Goal: high-quality YouTube audio backups via `yt-dlp`
- Implemented:
  - `/youtube/search`, `/youtube/download`, `/youtube/status/{download_id}`
  - cookie-based runtime wiring
  - strict invalid-cookie fail-fast behavior
- Validated status:
  - search + selection + download + status polling working end-to-end
  - output lands in language-specific `YouTube_Music` folders with metadata + embedded cover art
- Runtime requirement:
  - valid `youtube_cookies.txt` must be present and refreshed periodically

---

## Phase 8: Google Ecosystem Automation (Implemented — 14 Mar 2026)
- Goal: give Raven full programmatic control over Sam's Google account (Gmail, Drive, Calendar, Sheets)
- Built and validated by Claude Sonnet working on `D:\RAVENs\My_Google_AI_Assistant\`
- Full skill reference: `D:\RAVENs\My_Google_AI_Assistant\SKILL.md`

### What is built and confirmed working:

**Gmail:**
- `gmail_reader.py` — list inbox, read full email body, count unread by label, list labels
- `gmail_worker.py` — bulk trash / archive / mark-read / permanent-delete by search query (scale-safe, Python pagination, no gws `--page-all` hang risk)
- `gmail_analyzer.py` — rank senders by volume, group by domain, find old emails (audit tool)
- `gmail_filters.py` — create/list/delete Gmail filter rules (auto-trash future emails from an address or domain)

**Google Drive:**
- `drive_worker.py` — search files, trash file, batch-trash by query, empty trash, find duplicates (MD5 grouping, full Drive scan)

**Google Calendar:**
- `calendar_worker.py` — today's events, week view, list by date range, search by keyword, create events (with IST time, location, description), delete events

**Chronicle (Google Sheets as personal data layer):**
- `sheets_worker.py` — read/append/write to Sam's Chronicle spreadsheet (7 tabs: MediaLog, Finance, Queue, Preferences, ActionLog, Goals, Contacts)
- `lastfm_sync.py` — pulls Last.fm scrobbles → MediaLog (incremental, state-file based)
- `trakt_sync.py` — pulls Trakt.tv watched history + watchlist → MediaLog + Queue
- `bank_email_parser.py` — parses bank transaction emails (Fi Money, DBS, Equitas) → Finance tab
- `chronicle_sync.py` — master orchestrator, runs all three sources in sequence

**VPS Automation (live crons on 69.62.73.167):**
- `0 1 * * *` IST → `chronicle_sync.py` (daily 1 AM sync: Last.fm + Trakt + bank emails)
- `0 13 * * *` IST → `chronicle_sync.py` (daily 1 PM sync: keeps Last.fm fresh mid-day)
- `30 1 * * *` IST → `chronicle_briefing.py` (morning briefing → Telegram at 7 AM IST)

**Morning Briefing (LIVE):**
- `chronicle_briefing.py` — sends daily 7 AM Telegram message to Sam with:
  - Music: track count + top artists from Last.fm
  - Watched: movies/TV from Trakt
  - Finance: last 5 transactions across all banks
  - VPS Health: container status, disk, RAM, GDrive mount
  - Sync status: per-source success/failure

### Auth and platform notes:
- All scripts use direct Google API calls via `httpx` — no gws/Node.js required on VPS
- Single `credentials.json` (client_id + client_secret + refresh_token) covers all Google APIs
- OAuth scopes: `https://mail.google.com/`, `https://www.googleapis.com/auth/drive`, `https://www.googleapis.com/auth/calendar`, `https://www.googleapis.com/auth/gmail.settings.basic`
- VPS uses `GOOGLE_CREDENTIALS_FILE=/root/apps/chronicle/credentials.json`

---

## Phase 9: Two-Way Telegram Bot — Raven as Interactive Google Assistant (Planned)
- Goal: Sam can message Raven on Telegram to query or act on his Google ecosystem
- Examples: "What did I spend this week?", "What's on my calendar tomorrow?", "Create an event for dentist Friday 11am", "Trash all emails from X"
- Architecture: Telegram bot webhook listener on VPS → routes intent → calls existing Google scripts → replies to Sam
- All underlying tools (Gmail, Calendar, Chronicle, Drive) are already built and working
- This phase is purely the conversational routing + Telegram listener layer on top
- Dependency: OpenClaw Telegram channel wiring (currently pending fresh bot token per PROJECT_PLAN note)

---

## Phase 7: Recommendation Engine (Phase 1 Implemented)
- Goal: personalized "Raven Recommends"
- Implemented in phase 1:
  - central recommendation engine inside `sam-media-api`
  - Postgres-backed `reco` schema
  - activity ingestion from Jellyfin, Navidrome, and Kavita
  - weekly digest generation
  - on-demand recommendation queries
  - optional LLM theme synthesis through OpenRouter
  - VPS cron schedule for daily ingest + Sunday digest generation
- Deferred to later phases:
  - Audiobookshelf ingestion
  - Telegram delivery through OpenClaw
  - vector search / semantic memory
  - recommendation-triggered acquisition flows
