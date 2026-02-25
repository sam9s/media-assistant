# Sam's Media Assistant (SamAssist)

**Project:** Hybrid AI Media Manager
**Approach:** Custom Media API + Raven (OpenClaw) Integration
**VPS:** sam9scloud.in (IP: 69.62.73.167)
**Deployed path on VPS:** `/root/apps/sam-media-api/`
**Version:** 2.1.0
**Status:** LIVE — Deployed and tested (2026-02-22)

---

## 1. WHAT IS BUILT

A lean FastAPI service (`sam-media-api`) that gives Raven (the AI assistant) full control over:
- Searching torrent trackers (Jackett + iptorrents) for movies, TV, music
- Adding torrents to qBittorrent with correct save paths and title tags
- Receiving a webhook from qBittorrent on download completion → renaming → copying to Jellyfin library / Google Drive
- Triggering Jellyfin library refresh so content appears immediately

**What was NOT built (future phases):**
- Immich photo management
- AzuraCast radio control
- Kavita/Audiobookshelf book search
- Recommendation engine

---

## 2. ACTUAL SYSTEM ARCHITECTURE

```
Raven (OpenClaw)
    │
    ▼ HTTP + X-API-Key header
sam-media-api  (port 8765 on VPS host, :8000 inside container)
    │
    ├── /search  ──▶  Jackett (internal Docker: jackett:9117)
    │              ──▶  iptorrents.com RSS (external)
    │              ──▶  TMDB API (external metadata)
    │
    ├── /download ──▶  qBittorrent API (https://downloads.sam9scloud.in)
    │                  saves to /downloads/complete/{category}/
    │                  stores "Title|Year" as torrent tag
    │
    ├── /status  ──▶  qBittorrent active downloads
    │              ──▶  Jellyfin search (optional title check)
    │
    └── /complete  ◀── qBittorrent fires this on torrent completion
                       1. Reads "Title|Year" tag from qBT
                       2. Renames file: messy_name.mkv → Title (Year).mkv
                       3. shutil.copy2 to /mnt/cloud/gdrive/Media/{category}/
                          (= Google Drive FUSE mount = Jellyfin library simultaneously)
                       4. Jellyfin refresh → appears in library immediately
```

### Key infrastructure insight
`/mnt/cloud/gdrive/Media` on the VPS host is a **live rclone FUSE mount** of Google Drive.
Jellyfin mounts this same path as `/media` inside its container.
**One `shutil.copy2` call serves both Google Drive archive AND Jellyfin library** — no separate rclone subprocess needed.

---

## 3. ACTUAL FILE STRUCTURE (local dev at D:\RAVENs\media_assistant\)

```
media_assistant/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI — all endpoints, SAVE_PATHS, MEDIA_PATHS, /complete logic
│   ├── config.py        # Pydantic Settings — reads .env
│   ├── jackett.py       # Jackett search client
│   ├── iptorrents.py    # iptorrents RSS parser
│   ├── tmdb.py          # TMDB metadata client
│   ├── qbittorrent.py   # qBittorrent API client (add torrent, get tags, active downloads)
│   └── jellyfin.py      # Jellyfin API client (search, refresh_library)
├── skills/
│   └── media-assistant/
│       └── SKILL.md     # Raven skill — how Raven calls this API
├── docs/
│   ├── PROJECT_PLAN.md        ← THIS FILE
│   └── INFRASTRUCTURE_AUDIT.md
├── docker-compose.yml   # sam-media-api + jackett containers
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
  "quality": "1080p",        // optional — filters results containing this string
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
- Renames file(s) to `Title (Year).ext`
- Copies to Google Drive FUSE mount (= Jellyfin library)
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

## 6. VPS PATH MAP (critical — do not confuse these)

| What | VPS host path | Container-internal path | Notes |
|---|---|---|---|
| qBittorrent downloads | `/srv/downloads` | `/downloads` (in qBT + our container) | Both containers mount this |
| Completed downloads | `/srv/downloads/complete/Movies/Hollywood/` | `/downloads/complete/Movies/Hollywood/` | Our API renames files here |
| Google Drive FUSE | `/mnt/cloud/gdrive` | `/mnt/cloud/gdrive` (same) | rclone FUSE, allow_other |
| Jellyfin library | `/mnt/cloud/gdrive/Media/` | `/media/` (inside Jellyfin container) | One write = gdrive + Jellyfin |
| qBT watch folder | `/srv/torrents/watch` | `/watch` (in qBT container) | Manual .torrent drops |
| Our API source | `/root/apps/sam-media-api/` | — | Deployed here on VPS |

---

## 7. DOCKER COMPOSE (current production state)

```yaml
services:
  sam-media-api:
    build: .
    container_name: sam-media-api
    restart: unless-stopped
    ports:
      - "8765:8000"   # 0.0.0.0 binding — qBittorrent can call 172.17.0.1:8765
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

## 8. ENVIRONMENT VARIABLES (.env — never commit)

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
```

---

## 9. WHAT WAS TESTED AND CONFIRMED WORKING (2026-02-22)

| Test | Result |
|---|---|
| `GET /health` returns `{"status":"ok"}` | PASS |
| `POST /complete` with dummy 1MB `.mkv` file | PASS |
| File renamed: `Test.Movie.2024.1080p.BluRay.mkv` → `Test Movie (2024).mkv` | PASS |
| File copied to `/mnt/cloud/gdrive/Media/Movies/Hollywood/` | PASS |
| Jellyfin `refresh_library()` fired | PASS |
| Cleanup of test files | Done |

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

## 10. PENDING — MANUAL SETUP IN qBITTORRENT UI

These two settings must be configured once in the qBittorrent web UI (`downloads.sam9scloud.in`):

### A. "Run on completion" webhook
Settings → Downloads → **Run External Program on torrent completion:**
```
curl -s -X POST http://172.17.0.1:8765/complete -H "X-API-Key: <API_KEY>" -H "Content-Type: application/json" -d "{\"name\":\"%N\",\"category\":\"%L\",\"content_path\":\"%F\",\"info_hash\":\"%I\"}"
```
- `172.17.0.1` = Docker bridge gateway (host IP as seen from inside qBT container)
- `%N` = torrent name, `%L` = category label, `%F` = file/folder path, `%I` = info hash
- Fires **after download completes**, not when added

### B. 30-day seeding auto-delete
Settings → BitTorrent → Seeding Limits:
- Enable: When seeding time reaches **43200 minutes** (30 days)
- Action: **Remove torrent and delete data**

**Effect:** qBittorrent keeps seeding for 30 days, then deletes its local copy from `/srv/downloads/complete/`.
Google Drive and Jellyfin keep the file permanently (copied at completion time).

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
# → {"status":"ok"}
```

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
- **Old media-assistant project**: `/root/apps/media-assistant/` — older, heavier version with Postgres+Redis. Our project is the lean replacement at `/root/apps/sam-media-api/`.
- **rclone**: Already installed at `/usr/bin/rclone`. `gdrive:` remote already configured. FUSE-mounted at `/mnt/cloud/gdrive` with `allow_other` so Docker containers can write to it.
- **Jackett**: Running inside the same Docker Compose stack, reachable as `http://jackett:9117` from `sam-media-api`.

---

## 14. FUTURE PHASES (not yet built)

- **Immich photo search** — `GET /photos/search`
- **AzuraCast radio control** — `POST /radio/play`, `GET /radio/nowplaying`
- **Kavita / Audiobookshelf** — book/audiobook search
- **Recommendation engine** — "suggest something like Blade Runner"
- **Dashboard widget** — embedded chat UI in RamenUI
