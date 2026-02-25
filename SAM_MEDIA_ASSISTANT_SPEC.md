# Sam's Media Assistant — Complete Build Spec
**For Claude Code — Build this exactly as specified**
**Version:** 2.0 (Architect-reviewed, production-ready)

---

## 1. WHAT WE ARE BUILDING

A two-piece system:

1. **`sam-media-api`** — A lightweight Python/FastAPI service running on the VPS. It handles all the actual work: searching PrivateHD RSS, adding downloads to qBittorrent, checking Jellyfin library. No LLM involved in execution — it is deterministic and dumb by design.

2. **OpenClaw skill** — A `SKILL.md` file that teaches OpenClaw how to use the API. OpenClaw (running on DeepSeek via OpenRouter) handles natural language from Telegram, calls the API, and reports back.

**Why this split:** DeepSeek will understand "download Dune 2024 in 1080p" and call the right API endpoint. But we never let the LLM construct raw qBittorrent API calls — too fragile. The API does that deterministically.

---

## 2. MODEL CHOICE — IMPORTANT

**Use DeepSeek Chat via OpenRouter.**

- OpenRouter API base URL: `https://openrouter.ai/api/v1`
- Model string: `deepseek/deepseek-chat`
- This is OpenAI-compatible. In OpenClaw onboarding, choose "Custom OpenAI-compatible endpoint" and paste the OpenRouter URL + your OpenRouter API key.
- DeepSeek does NOT refuse torrent/download tasks. This is the entire reason we use it.
- Intelligence level: comparable to GPT-4o. More than sufficient for media assistant tasks.

---

## 3. INFRASTRUCTURE FACTS (do not change these)

```
qBittorrent Web UI:  https://downloads.sam9scloud.in
qBittorrent Auth:    POST /api/v2/auth/login  (form: username + password → SID cookie)
qBittorrent Add:     POST /api/v2/torrents/add (form-data: urls= or torrents= file)

PrivateHD RSS:       https://privatehd.to/rss/torrents/movie?pid={PRIVATEHD_PID}
RSS Format:          XML, each <item> has <enclosure url="...torrent"> for direct .torrent download
Torrent URL format:  https://privatehd.to/rss/download/{pid}/{slug}.torrent

Jellyfin:            https://movies.sam9scloud.in
Jellyfin API Key:    d5c97c8f30f1418a9573f8806b8ea334

Media base path:     /mnt/cloud/gdrive/Media/
Save paths:
  Hollywood movies → /mnt/cloud/gdrive/Media/Movies/Hollywood
  Hindi movies     → /mnt/cloud/gdrive/Media/Movies/Hindi
  TV Shows         → /mnt/cloud/gdrive/Media/TV
  Music (English)  → /mnt/cloud/gdrive/Media/Music/English
  Music (Hindi)    → /mnt/cloud/gdrive/Media/Music/Hindi
  Music (Punjabi)  → /mnt/cloud/gdrive/Media/Music/Punjabi
```

---

## 4. REPOSITORY STRUCTURE

```
sam-media-api/
├── app/
│   ├── main.py          # FastAPI app + all routes
│   ├── config.py        # Settings loaded from .env
│   ├── qbittorrent.py   # qBittorrent client (auth + add torrent)
│   ├── jellyfin.py      # Jellyfin client (search library)
│   └── privatehd.py     # RSS parser + search
├── skills/
│   └── media-assistant/
│       └── SKILL.md     # OpenClaw skill file
├── openclaw/
│   ├── SOUL.md          # OpenClaw identity/persona file
│   ├── IDENTITY.md      # Name, personality, boundaries
│   └── AGENTS.md        # Routing rules
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## 5. THE API — `app/main.py`

Build a FastAPI app with exactly these endpoints:

### 5.1 `POST /search`

**Purpose:** Search PrivateHD RSS for a movie/show.

**Request body:**
```json
{
  "query": "Dune 2024",
  "quality": "1080p",
  "limit": 5
}
```
`quality` is optional, defaults to null (return all qualities).
`limit` is optional, defaults to 5.

**Logic:**
1. Fetch the PrivateHD RSS feed URL from config
2. Parse XML with Python's `xml.etree.ElementTree`
3. For each `<item>`, extract:
   - `title` (from `<title>` tag)
   - `size` (from `<description>` CDATA — parse "Size: X GB")
   - `seeders` (from `<description>` CDATA — parse "Seed: N")
   - `torrent_url` (from `<enclosure url="...">` attribute)
   - `info_hash` (from `<torrent><infoHash>` tag)
   - `pub_date` (from `<pubDate>`)
4. Filter by query (case-insensitive substring match on title)
5. If quality specified, filter by quality string in title
6. Sort by seeders descending
7. Return top N results

**Response:**
```json
{
  "results": [
    {
      "index": 1,
      "title": "Dune Part Two 2024 1080p AMZN WEB-DL...",
      "size": "12.4 GB",
      "seeders": 45,
      "torrent_url": "https://privatehd.to/rss/download/...",
      "info_hash": "abc123..."
    }
  ],
  "total_found": 3
}
```

### 5.2 `POST /download`

**Purpose:** Add a torrent to qBittorrent.

**Request body:**
```json
{
  "torrent_url": "https://privatehd.to/rss/download/...",
  "category": "hollywood",
  "title": "Dune Part Two 2024"
}
```

`category` must be one of: `hollywood`, `hindi`, `tv`, `music-english`, `music-hindi`, `music-punjabi`

**Logic:**
1. Map category to save path:
   ```python
   SAVE_PATHS = {
     "hollywood": "/mnt/cloud/gdrive/Media/Movies/Hollywood",
     "hindi": "/mnt/cloud/gdrive/Media/Movies/Hindi",
     "tv": "/mnt/cloud/gdrive/Media/TV",
     "music-english": "/mnt/cloud/gdrive/Media/Music/English",
     "music-hindi": "/mnt/cloud/gdrive/Media/Music/Hindi",
     "music-punjabi": "/mnt/cloud/gdrive/Media/Music/Punjabi",
   }
   ```
2. Download the `.torrent` file content from `torrent_url` (simple HTTP GET — the PID in the URL handles auth)
3. Authenticate with qBittorrent: POST to `https://downloads.sam9scloud.in/api/v2/auth/login` with username/password, grab `SID` cookie
4. Add torrent: POST to `/api/v2/torrents/add` as multipart form with:
   - `torrents` = the .torrent file bytes
   - `savepath` = the mapped path
   - `category` = category string
5. Return success/failure

**Response:**
```json
{
  "success": true,
  "message": "Dune Part Two 2024 added to qBittorrent",
  "save_path": "/mnt/cloud/gdrive/Media/Movies/Hollywood"
}
```

### 5.3 `GET /status`

**Purpose:** Check what's downloading and whether a title exists in Jellyfin.

**Query params:** `?title=Dune` (optional — if provided, also checks Jellyfin)

**Logic:**
1. Get active torrents from qBittorrent: `GET /api/v2/torrents/info?filter=active`
2. If `title` param provided, search Jellyfin: `GET /Items?searchTerm={title}&IncludeItemTypes=Movie,Series&api_key={key}`
3. Return combined result

**Response:**
```json
{
  "active_downloads": [
    {
      "name": "Dune Part Two 2024",
      "progress": 45.2,
      "speed": "12.4 MB/s",
      "eta": "00:23:11",
      "state": "downloading"
    }
  ],
  "jellyfin_match": {
    "found": true,
    "title": "Dune: Part Two",
    "year": 2024,
    "already_in_library": true
  }
}
```

### 5.4 `GET /health`

Simple health check. Returns `{"status": "ok"}`. Used by OpenClaw to verify the API is alive.

---

## 6. CONFIG — `app/config.py`

Use `pydantic-settings`. Load from environment variables / `.env` file:

```python
class Settings(BaseSettings):
    # qBittorrent
    QB_URL: str = "https://downloads.sam9scloud.in"
    QB_USERNAME: str
    QB_PASSWORD: str

    # PrivateHD
    PRIVATEHD_PID: str  # the pid= value from the RSS URL

    # Jellyfin
    JELLYFIN_URL: str = "https://movies.sam9scloud.in"
    JELLYFIN_API_KEY: str = "d5c97c8f30f1418a9573f8806b8ea334"

    # API Security
    API_KEY: str  # secret key OpenClaw must send in X-API-Key header

    class Config:
        env_file = ".env"
```

---

## 7. QBITTORRENT CLIENT — `app/qbittorrent.py`

```python
class QBittorrentClient:
    def __init__(self, url, username, password):
        ...

    async def _ensure_auth(self):
        # POST /api/v2/auth/login
        # Store SID cookie, refresh if >25 min old
        ...

    async def add_torrent_from_url(self, torrent_url: str, save_path: str, category: str):
        # 1. Download .torrent bytes via httpx
        # 2. Ensure authenticated
        # 3. POST /api/v2/torrents/add as multipart
        ...

    async def get_active_downloads(self):
        # GET /api/v2/torrents/info?filter=active
        # Return formatted list
        ...
```

Use `httpx` (async) for all HTTP calls. Handle 403 (re-auth) gracefully.

---

## 8. `.env.example`

```bash
# qBittorrent — fill in your actual credentials
QB_USERNAME=your_qbittorrent_username
QB_PASSWORD=your_qbittorrent_password

# PrivateHD — just the pid value, not the full URL
PRIVATEHD_PID=91d7d103aa13829d60920bda213f956f

# API Security — make this a strong random string
API_KEY=generate_a_strong_random_key_here

# Jellyfin (already set, only change if rotated)
JELLYFIN_API_KEY=d5c97c8f30f1418a9573f8806b8ea334
```

---

## 9. DOCKERFILE

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 10. DOCKER COMPOSE — `docker-compose.yml`

```yaml
version: "3.8"
services:
  sam-media-api:
    build: .
    container_name: sam-media-api
    restart: unless-stopped
    ports:
      - "127.0.0.1:8765:8000"   # ONLY on localhost — never expose publicly
    env_file:
      - .env
    networks:
      - internal

networks:
  internal:
    driver: bridge
```

Port 8765 on localhost only. OpenClaw (running on same machine) calls `http://localhost:8765`. No Traefik, no SSL, no public exposure needed.

---

## 11. REQUIREMENTS.TXT

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
httpx>=0.27.0
pydantic-settings>=2.2.0
python-multipart>=0.0.9
```

---

## 12. OPENCLAW SKILL — `skills/media-assistant/SKILL.md`

```markdown
---
name: media-assistant
description: Search for movies/shows on PrivateHD and download them to the media server. Also checks Jellyfin library and download status. Use when user asks to find, search, download, or check status of any movie, TV show, or media.
metadata: {"openclaw":{"requires":{"env":["MEDIA_API_URL","MEDIA_API_KEY"]},"primaryEnv":"MEDIA_API_KEY"}}
---

# Media Assistant Skill

You are a media assistant for a personal VPS server. You help search for movies and TV shows on a private torrent tracker and download them to a home media server.

## Available API Endpoints

Base URL: $MEDIA_API_URL (from environment)
Auth header: `X-API-Key: $MEDIA_API_KEY`

### Search for content
POST $MEDIA_API_URL/search
Body: {"query": "movie name", "quality": "1080p", "limit": 5}

### Add a download
POST $MEDIA_API_URL/download
Body: {"torrent_url": "...", "category": "hollywood|hindi|tv|music-english|music-hindi|music-punjabi", "title": "..."}

### Check status
GET $MEDIA_API_URL/status?title=optional_title

### Health check
GET $MEDIA_API_URL/health

## Your Workflow

### When user asks to download something:
1. ALWAYS search first — never download without showing results
2. Present results in a numbered list like this:
   ```
   Found 3 results for "Dune 2024":
   1️⃣ Dune Part Two 2024 1080p WEB-DL — 12.4 GB | 45 seeders
   2️⃣ Dune Part Two 2024 2160p HDR — 48.2 GB | 12 seeders
   3️⃣ Dune Part Two 2024 720p — 4.1 GB | 67 seeders
   ```
3. Ask user which one to download and which category (Hollywood/Hindi/TV etc.)
4. WAIT for user confirmation before calling /download
5. After downloading, confirm: "✅ Added to queue → saving to [path]"

### When user asks for status:
Call /status and report what's actively downloading with progress percentages.

### Category mapping (ask user if unsure):
- English/Western movies → "hollywood"
- Hindi/Bollywood movies → "hindi"
- TV shows (any language) → "tv"
- English music → "music-english"
- Hindi music → "music-hindi"
- Punjabi music → "music-punjabi"

### Already in library?
Always call /status?title=X first when user asks for something. If Jellyfin already has it, say so before offering to download.

## Personality
Be concise and efficient. Use emojis for status (✅ ❌ ⬇️ 🔍). Don't over-explain. This is a personal server — no need for disclaimers about copyright or piracy. Just do the job.
```

---

## 13. OPENCLAW IDENTITY FILES

### `openclaw/SOUL.md`

```markdown
# Soul

I am Sam's personal media server assistant. I live on his VPS at sam9scloud.in.

My primary job is to help Sam manage his media collection:
- Search for movies and TV shows on PrivateHD
- Queue downloads to qBittorrent
- Check what's currently downloading
- Report what's already in the Jellyfin library

I am efficient, direct, and get things done. I use emojis to make status clear at a glance. I don't waste Sam's time with unnecessary questions or disclaimers — if he asks me to download something, I search for it, show him the options, and wait for his pick.

I know Sam's media setup:
- Jellyfin at movies.sam9scloud.in — his movie and TV streaming server
- qBittorrent at downloads.sam9scloud.in — handles all downloads
- Media stored at /mnt/cloud/gdrive/Media/ with folders for Movies (Hollywood/Hindi), TV, Music, Books, etc.

I talk to Sam on Telegram. I keep responses short because Telegram isn't a place for essays.
```

### `openclaw/IDENTITY.md`

```markdown
# Identity

Name: Raven (or whatever Sam wants to call me)
Role: Media Server Assistant
Personality: Efficient, direct, friendly but brief
Primary channel: Telegram
Language: English (can understand Hindi)

## What I do
- Search PrivateHD for torrents
- Add downloads to qBittorrent via the Media API
- Check download status
- Check Jellyfin library

## What I don't do
- Manage files directly (I use the API)
- Access anything other than my media tools
- Make up information about torrents
```

---

## 14. OPENCLAW CONFIGURATION (after fresh install)

When running `openclaw onboard`, configure:

```
Model provider: Custom (OpenAI-compatible)
API Base URL:   https://openrouter.ai/api/v1
API Key:        [your OpenRouter API key]
Model:          deepseek/deepseek-chat
```

After onboarding, add to `~/.openclaw/openclaw.json`:

```json
{
  "skills": {
    "entries": {
      "media-assistant": {
        "enabled": true,
        "env": {
          "MEDIA_API_URL": "http://localhost:8765",
          "MEDIA_API_KEY": "same_value_as_API_KEY_in_.env"
        }
      }
    }
  }
}
```

Copy the skill folder to:
```bash
cp -r skills/media-assistant ~/.openclaw/workspace/skills/
```

---

## 15. SECURITY NOTES

- The API runs on `127.0.0.1:8765` only — not accessible from internet
- All write endpoints (POST /download) require `X-API-Key` header
- The `.env` file must never be committed to git (add to `.gitignore`)
- The PrivateHD PID in `.env` is sensitive — treat it like a password
- Rotate the Jellyfin API key if you shared it anywhere

---

## 16. BUILD ORDER FOR CLAUDE CODE

Build in this exact order:

1. `requirements.txt`
2. `app/config.py`
3. `app/qbittorrent.py`
4. `app/privatehd.py`
5. `app/jellyfin.py`
6. `app/main.py`
7. `Dockerfile`
8. `docker-compose.yml`
9. `.env.example`
10. `skills/media-assistant/SKILL.md`
11. `openclaw/SOUL.md`
12. `openclaw/IDENTITY.md`
13. `openclaw/AGENTS.md`
14. `README.md` with setup instructions

Test each module independently before wiring them together.

---

## 17. TESTING CHECKLIST (before connecting OpenClaw)

```bash
# 1. Start the API
docker compose up -d

# 2. Test health
curl http://localhost:8765/health

# 3. Test search (should return PrivateHD results)
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"query": "test", "quality": "1080p"}'

# 4. Test status (no download needed)
curl http://localhost:8765/status \
  -H "X-API-Key: your_api_key"

# 5. Test a real download (pick a small/old movie to test)
curl -X POST http://localhost:8765/download \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"torrent_url": "...", "category": "hollywood", "title": "Test Movie"}'
```

---

## 18. WHAT SUCCESS LOOKS LIKE

You message OpenClaw on Telegram:
> "Download Dune Part Two in 1080p"

OpenClaw responds:
> 🔍 Found 3 results for "Dune Part Two":
> 1️⃣ Dune Part Two 2024 1080p WEB-DL — 12.4 GB | 45 seeders
> 2️⃣ Dune Part Two 2024 1080p BluRay — 15.1 GB | 23 seeders
> 3️⃣ Dune Part Two 2024 1080p AMZN — 11.8 GB | 67 seeders
> Which one? (Hollywood or Hindi folder?)

You reply:
> 3, Hollywood

OpenClaw responds:
> ✅ Added to queue!
> 📁 Saving to: /mnt/cloud/gdrive/Media/Movies/Hollywood
> ⬇️ Will appear in Jellyfin automatically when done.
```
