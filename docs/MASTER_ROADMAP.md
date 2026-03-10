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
