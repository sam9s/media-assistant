# Sam's Media Assistant

A two-piece system for managing your personal media server via Telegram:

1. **`sam-media-api`** — FastAPI backend running in Docker. Searches PrivateHD, adds downloads to qBittorrent, checks Jellyfin.
2. **OpenClaw skill** — Teaches OpenClaw (DeepSeek via OpenRouter) to understand your Telegram messages and call the API.

---

## Quick Start

### 1. Clone and configure

```bash
git clone <this-repo>
cd sam-media-api

cp .env.example .env
nano .env   # fill in QB_USERNAME, QB_PASSWORD, and set a strong API_KEY
```

Your `.env` should look like:

```bash
QB_USERNAME=your_actual_qbittorrent_username
QB_PASSWORD=your_actual_qbittorrent_password
PRIVATEHD_PID=91d7d103aa13829d60920bda213f956f
API_KEY=some_long_random_string_you_generate
JELLYFIN_API_KEY=d5c97c8f30f1418a9573f8806b8ea334
```

> **Never commit `.env` to git.** It is in `.gitignore` already.

### 2. Start the API

```bash
docker compose up -d
```

The API runs at `http://localhost:8765` (localhost only — not exposed to the internet).

### 3. Verify it works

```bash
# Health check (no auth needed)
curl http://localhost:8765/health

# Search test
curl -X POST http://localhost:8765/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your_api_key" \
  -d '{"query": "Dune", "quality": "1080p", "limit": 3}'

# Status check
curl http://localhost:8765/status \
  -H "X-API-Key: your_api_key"
```

---

## API Reference

All endpoints except `/health` require the `X-API-Key` header.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Health check — `{"status": "ok"}` |
| POST | `/search` | Search PrivateHD RSS |
| POST | `/download` | Add torrent to qBittorrent |
| GET | `/status` | Active downloads + optional Jellyfin check |

### POST /search

```json
{
  "query": "Dune 2024",
  "quality": "1080p",
  "limit": 5
}
```

### POST /download

```json
{
  "torrent_url": "https://privatehd.to/rss/download/...",
  "category": "hollywood",
  "title": "Dune Part Two 2024"
}
```

Valid categories: `hollywood`, `hindi`, `tv`, `music-english`, `music-hindi`, `music-punjabi`

### GET /status?title=Dune

Returns active downloads from qBittorrent and, if `title` is provided, checks Jellyfin.

---

## Save Paths

| Category | Path |
|---|---|
| hollywood | `/mnt/cloud/gdrive/Media/Movies/Hollywood` |
| hindi | `/mnt/cloud/gdrive/Media/Movies/Hindi` |
| tv | `/mnt/cloud/gdrive/Media/TV` |
| music-english | `/mnt/cloud/gdrive/Media/Music/English` |
| music-hindi | `/mnt/cloud/gdrive/Media/Music/Hindi` |
| music-punjabi | `/mnt/cloud/gdrive/Media/Music/Punjabi` |

---

## OpenClaw Setup

### 1. Onboard OpenClaw

Run `openclaw onboard` and configure:

```
Model provider: Custom (OpenAI-compatible)
API Base URL:   https://openrouter.ai/api/v1
API Key:        [your OpenRouter API key]
Model:          deepseek/deepseek-chat
```

### 2. Install the skill

```bash
cp -r skills/media-assistant ~/.openclaw/workspace/skills/
```

### 3. Add skill config to `~/.openclaw/openclaw.json`

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

### 4. Copy identity files

```bash
cp openclaw/SOUL.md ~/.openclaw/SOUL.md
cp openclaw/IDENTITY.md ~/.openclaw/IDENTITY.md
cp openclaw/AGENTS.md ~/.openclaw/AGENTS.md
```

---

## Security Notes

- API is bound to `127.0.0.1:8765` — not reachable from the internet
- `.env` is gitignored — never commit it
- PrivateHD PID is sensitive — treat it like a password
- Rotate `API_KEY` if you ever suspect it was exposed

---

## Infrastructure

| Service | URL |
|---|---|
| qBittorrent | https://downloads.sam9scloud.in |
| Jellyfin | https://movies.sam9scloud.in |
| This API | http://localhost:8765 |
