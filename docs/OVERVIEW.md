# Sam's Media Assistant (SamAssist)

**Version:** 2.2.0
**Status:** LIVE — Deployed and validated end-to-end
**VPS:** sam9scloud.in (69.62.73.167)
**API path on VPS:** `/root/apps/sam-media-api/`

---

## What Is This?

SamAssist is a personal AI-controlled media management system. It connects Sam's AI assistant (Raven) to his private torrent network, qBittorrent download client, Google Drive archive, and Jellyfin media server — turning a simple chat command into a fully automated download-to-library pipeline.

Sam says: *"Download Sinners 2025 in 1080p"*
Raven handles everything: searching, checking if it's already there, downloading, copying, renaming, and getting it into Jellyfin — automatically.

---

## What Is Built (v2.2.0)

### The Core Pipeline

```
Raven (AI assistant, via Telegram)
    │
    ▼  HTTP + X-API-Key
sam-media-api  ← FastAPI service, port 8765 on VPS
    │
    ├── /search  ──► Jackett (private trackers via Docker: jackett:9117)
    │              ──► iptorrents (private tracker, RSS search)
    │              ──► TMDB (movie metadata, posters, ratings, IMDb links)
    │
    ├── /download ──► qBittorrent (downloads.sam9scloud.in)
    │                  Tags torrent as "Title|Year" for later clean renaming
    │                  Saves to /downloads/complete/{category}/
    │
    ├── /status  ──► Active qBittorrent downloads (progress, speed, ETA)
    │              ──► Jellyfin library check (is it already there?)
    │
    └── /complete  ◄── qBittorrent webhook on download completion
                       Reads "Title|Year" tag → clean destination name
                       Copies to /mnt/cloud/gdrive/Media/{category}/
                       (= Google Drive archive AND Jellyfin library simultaneously)
                       Fires Jellyfin refresh → content appears immediately
```

### API Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | No auth. Returns `{"status": "ok"}` — liveness check |
| `POST /search` | Search Jackett + iptorrents + enrich with TMDB metadata |
| `POST /download` | Queue a torrent to qBittorrent with correct category path |
| `GET /status` | Live download progress + optional Jellyfin library check |
| `POST /complete` | qBittorrent webhook — copy + rename + Jellyfin refresh |

### Media Categories

| Content | Category Key | Google Drive + Jellyfin Path |
|---|---|---|
| English/Hollywood movies | `hollywood` | `Media/Movies/Hollywood` |
| Hindi/Bollywood movies | `hindi` | `Media/Movies/Hindi` |
| English TV shows | `tv-hollywood` | `Media/TV/Hollywood` |
| Hindi/Indian TV shows | `tv-indian` | `Media/TV/Indian` |
| English music | `music-english` | `Media/Music/English` |
| Hindi music | `music-hindi` | `Media/Music/Hindi` |
| Punjabi music | `music-punjabi` | `Media/Music/Punjabi` |

### Key Infrastructure

| Component | Where |
|---|---|
| sam-media-api | Docker container on VPS, port 8765 |
| Jackett | Docker container (internal), port 9117 |
| FlareSolverr | Docker container (Cloudflare bypass for Jackett) |
| qBittorrent | `downloads.sam9scloud.in` |
| Jellyfin | `movies.sam9scloud.in` |
| Google Drive | rclone FUSE mount at `/mnt/cloud/gdrive` on VPS host |
| Raven (AI) | OpenClaw instance on VPS, talks to Sam via Telegram |
| Media Dashboard | Ramen Launchpad at `/root/apps/ramen-launchpad` |

### Smart Design Decisions

- **One write = Google Drive + Jellyfin**: The rclone FUSE mount means a single `shutil.copy2` archives the file to Drive and makes it stream-ready in Jellyfin simultaneously.
- **Source files stay untouched**: qBittorrent source paths are never renamed. Only the destination copy gets the clean `Title (Year).ext` name. Seeding continues uninterrupted.
- **30-day auto-delete**: qBittorrent seeds for 30 days, then deletes local source copies. Google Drive and Jellyfin keep the file permanently.
- **Webhook binding**: API binds to `0.0.0.0:8765` so qBittorrent's container can call `172.17.0.1:8765/complete` via Docker bridge.

### Raven / OpenClaw Configuration

Located in `openclaw/` and `skills/`:

| File | Purpose |
|---|---|
| `openclaw/IDENTITY.md` | Who Raven is and what it does |
| `openclaw/SOUL.md` | Raven's deep knowledge of Sam's setup and personality |
| `openclaw/AGENTS.md` | Skill routing — which intent goes to which skill |
| `openclaw/HEARTBEAT.md` | Proactive monitoring schedule and alert rules |
| `skills/media-assistant/SKILL.md` | Full API usage guide for media operations |
| `skills/vps-health/SKILL.md` | Full VPS health monitoring — all 34 containers + system resources |

---

## What Is NOT Yet Built (Future Phases)

| Phase | What | Status |
|---|---|---|
| Phase 2 | Immich photo management — `GET /photos/search` | Planned |
| Phase 3 | AzuraCast radio control — `POST /radio/play`, `GET /radio/nowplaying` | Planned |
| Phase 4 | Kavita / Audiobookshelf — book and audiobook search | Planned |
| Phase 5 | Recommendation engine — "suggest something like Blade Runner" | Planned |

---

## Where to Go for More Detail

| Document | Path | Purpose |
|---|---|---|
| Project Plan | `docs/PROJECT_PLAN.md` | Deep technical reference |
| DR Runbook | `docs/DR_RUNBOOK.md` | Backup and restore procedure |
| Media Skill | `skills/media-assistant/SKILL.md` | How Raven calls the media API |
| VPS Health Skill | `skills/vps-health/SKILL.md` | How Raven monitors the VPS |
