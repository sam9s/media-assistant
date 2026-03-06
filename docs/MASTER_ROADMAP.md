# Raven Media Server: Master Roadmap (March 2026)
**Owner:** Sam9s | **System:** Raven Automation Engine

## Phase 1-3: Completed and Stable
- Movies/TV: automated via qBittorrent
- E-books: Anna's Archive + Kavita pipeline working end-to-end
- Music (FLAC): Soulseek (slskd) pipeline working end-to-end with metadata enrichment

---

## Phase 3.5: Music Sharing (Pending)
- Goal: contribute back to P2P by sharing libraries
- Task: add read-only mounts for English/Hindi/Punjabi music into slskd container
- Network: open required Soulseek port on VPS firewall

---

## Phase 4: Audiobook Maven (Current Focus)
- Goal: automate audiobook acquisition and podcast management
- Task: connect Raven to Audiobookshelf API
- Logic: source audiobook-compatible formats, ingest, trigger Audiobookshelf scans

---

## Phase 5: YouTube/Opus Maven (Implemented and Validated)
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

## Phase 6: Recommendation Engine (Future)
- Goal: personalized "Raven Recommends"
- Logic: cross-reference current library with external trending/critic signals
