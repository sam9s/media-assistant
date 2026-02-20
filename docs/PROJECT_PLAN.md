# Sam's Media Assistant (SamAssist)

**Project:** Hybrid AI Media Manager  
**Approach:** Custom Media API + OpenClaw Integration  
**Repository:** `github.com/sam9s/media-assistant` (NEW - to be created)  
**Version:** 1.0.0  
**Status:** Planning Phase

---

## **1. EXECUTIVE SUMMARY**

Build a **Media Assistant API** that serves as the "brain" for media operations, free from LLM safety filters. OpenClaw acts as the conversational interface, routing sensitive queries to this API.

### **Key Philosophy:**
- **OpenClaw:** Handles chat, memory, scheduling, multi-channel (Telegram/WhatsApp)
- **Media Assistant API:** Handles "sensitive" operations (torrents, downloads) without censorship
- **Integration:** HTTP bridge between them

---

## **2. SYSTEM ARCHITECTURE**

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER INTERFACES                          │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │  Telegram   │  │  WhatsApp    │  │  Dashboard Widget    │   │
│  │  (OpenClaw) │  │  (OpenClaw)  │  │  (Embedded Chat)     │   │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬───────────┘   │
└─────────┼────────────────┼─────────────────────┼───────────────┘
          │                │                     │
          └────────────────┴─────────────────────┘
                              │
                              ▼ HTTP/REST
┌─────────────────────────────────────────────────────────────────┐
│                      OPENCLAW (BRIDGE)                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  • Routes "media queries" to Media Assistant API        │   │
│  │  • Maintains conversation context                       │   │
│  │  • Formats responses for user                           │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ HTTP/REST + Function Calling
┌─────────────────────────────────────────────────────────────────┐
│                   MEDIA ASSISTANT API                           │
│                    (Python/FastAPI)                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  Core Engine:                                           │   │
│  │  • No LLM for execution (no censorship)                 │   │
│  •  Structured tool calling (JSON schema)                  │   │
│  │  • Persistent media state (SQLite/Redis)                │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│         ┌────────────────────┼────────────────────┐             │
│         ▼                    ▼                    ▼             │
│  ┌─────────────┐     ┌──────────────┐    ┌──────────────┐      │
│  │   Tools     │     │    Tools     │    │    Tools     │      │
│  │  Library    │     │  Download    │    │   Photos     │      │
│  │  Manager    │     │   Manager    │    │   Manager    │      │
│  └──────┬──────┘     └──────┬───────┘    └──────┬───────┘      │
└─────────┼───────────────────┼───────────────────┼──────────────┘
          │                   │                   │
    ┌─────┴─────┐      ┌─────┴─────┐      ┌─────┴─────┐
    ▼           ▼      ▼           ▼      ▼           ▼
┌───────┐  ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐ ┌───────┐
│Jelly- │  │Kavita │ │Private│ │   qB  │ │Immich │ │Photo- │
│  fin   │  │       │ │  HD   │ │Torrent│ │       │ │search │
└───────┘  └───────┘ └───────┘ └───────┘ └───────┘ └───────┘
```

---

## **3. MODULE BREAKDOWN**

### **3.1 Media Assistant API (Core)**

```
/media-assistant-api
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI entry
│   ├── config.py            # Settings & env
│   ├── models.py            # Pydantic schemas
│   ├── database.py          # SQLite/Postgres
│   └── routers/
│       ├── library.py       # Jellyfin/Kavita queries
│       ├── downloads.py     # qBittorrent control
│       ├── torrents.py      # RSS search (PrivateHD)
│       ├── photos.py        # Immich queries
│       ├── radio.py         # AzuraCast control
│       └── stats.py         # Dashboard data
├── tools/
│   ├── __init__.py
│   ├── jellyfin_client.py
│   ├── qbittorrent_client.py
│   ├── immich_client.py
│   ├── azuracast_client.py
│   ├── rss_parser.py        # PrivateHD
│   └── tmdb_client.py
├── services/
│   ├── __init__.py
│   ├── search_service.py    # Cross-service search
│   └── recommendation.py    # Suggest movies
├── Dockerfile
├── requirements.txt
└── .env.example
```

### **3.2 OpenClaw Integration (Bridge)**

```
/openclaw-skills/
├── media_assistant/
│   ├── __init__.py
│   ├── skill.py             # OpenClaw skill definition
│   ├── client.py            # HTTP client to Media API
│   └── prompts.py           # System prompts for routing
└── README.md
```

### **3.3 Dashboard Widget (Frontend)**

```
/ramen-launchpad/ (existing repo)
└── src/
    └── components/
        └── ChatWidget.tsx   # Embedded chat UI
```

---

## **4. API ENDPOINTS SPECIFICATION**

### **4.1 Library Management**

```http
GET  /library/movies
     ?query=sci-fi&year=2024&sort=rating
     
GET  /library/shows
     ?query=stranger&status=watching
     
GET  /library/books
     ?query=fantasy&format=epub
     
GET  /library/audiobooks
     ?author=brandon+sanderson
     
GET  /library/stats
     Response: {movies: 1200, shows: 450, books: 300}
```

### **4.2 Torrent Operations**

```http
POST /torrents/search
     Body: {"query": "Dune 2024", "quality": "1080p"}
     Response: [{title, size, seeders, magnet}]

POST /torrents/add
     Body: {"magnet": "...", "category": "movies"}
     Response: {id, status: "queued"}

GET  /torrents/status
     Response: {active: 2, queue: [{name, progress, speed}]}
```

### **4.3 Photo Management**

```http
GET  /photos/search
     ?query="birthday+2024"&person="mom"
     
GET  /photos/albums
     
GET  /photos/recent
     ?count=10
```

### **4.4 Radio Control**

```http
POST /radio/play
     Body: {"station": "sam9s.radio"}
     
POST /radio/skip
     
GET  /radio/nowplaying
     Response: {title, artist, listeners}
     
POST /radio/request
     Body: {"song": "song name"}
```

### **4.5 Recommendations**

```http
GET  /recommend/movie
     ?based_on="Blade Runner 2049"
     
GET  /recommend/watchlist
     ?mood=sci-fi+adventure&time=evening
```

---

## **5. TOOL DEFINITIONS (for OpenClaw)**

```json
{
  "tools": [
    {
      "name": "search_media_library",
      "description": "Search movies, shows, books in Jellyfin/Kavita",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {"type": "string"},
          "media_type": {"enum": ["movie", "show", "book", "audiobook"]},
          "year": {"type": "integer"}
        }
      }
    },
    {
      "name": "search_torrents",
      "description": "Search PrivateHD for torrents (NO CENSORSHIP)",
      "parameters": {
        "query": {"type": "string"},
        "quality": {"enum": ["720p", "1080p", "2160p"]}
      }
    },
    {
      "name": "add_download",
      "description": "Add torrent to qBittorrent",
      "parameters": {
        "magnet": {"type": "string"},
        "save_path": {"type": "string"}
      }
    },
    {
      "name": "search_photos",
      "description": "Search photos in Immich",
      "parameters": {
        "query": {"type": "string"},
        "date_range": {"type": "string"}
      }
    },
    {
      "name": "control_radio",
      "description": "Play/stop radio or request songs",
      "parameters": {
        "action": {"enum": ["play", "stop", "skip", "request"]},
        "song": {"type": "string"}
      }
    }
  ]
}
```

---

## **6. SECURITY MODEL**

### **Threat Analysis:**
- **Risk:** API exposed to internet → Unauthorized torrent downloads
- **Risk:** OpenClaw compromise → Uncontrolled media access
- **Risk:** API key leaks → Jellyfin/qBittorrent access

### **Mitigations:**
```yaml
Authentication:
  - API Key required for all endpoints
  - Whitelist: Only OpenClaw IP + Dashboard
  - Rate limiting: 100 req/min per key

Authorization:
  - Read-only endpoints: No auth needed from local
  - Write endpoints (add_torrent): API key required
  - Admin endpoints: Master key required

Network:
  - Media API: localhost only (no public exposure)
  - OpenClaw → API: Internal Docker network
  - Dashboard → API: Localhost only
```

---

## **7. DEPLOYMENT STRATEGY**

### **Phase 1: Core API (Week 1)**
- [ ] FastAPI skeleton
- [ ] Docker containerization
- [ ] Jellyfin integration
- [ ] qBittorrent integration

### **Phase 2: Torrent Features (Week 2)**
- [ ] PrivateHD RSS parser
- [ ] Search & add torrents
- [ ] Download monitoring
- [ ] TMDB metadata enrichment

### **Phase 3: Media Features (Week 3)**
- [ ] Immich photo search
- [ ] Kavita book search
- [ ] AzuraCast radio control
- [ ] Recommendation engine

### **Phase 4: OpenClaw Integration (Week 4)**
- [ ] OpenClaw skill development
- [ ] Tool definitions
- [ ] Prompt engineering
- [ ] Testing & refinement

### **Phase 5: Dashboard (Week 5)**
- [ ] Chat widget UI
- [ ] Voice input (optional)
- [ ] Quick action buttons

---

## **8. ENVIRONMENT CONFIGURATION**

```bash
# .env (NEVER COMMIT)

# API Security
MEDIA_API_KEY=super_secret_key_here
MASTER_API_KEY=even_more_secret_master_key

# Jellyfin
JELLYFIN_URL=https://movies.sam9scloud.in
JELLYFIN_API_KEY=xxx

# qBittorrent
QBITTORRENT_URL=http://localhost:8088
QBITTORRENT_USER=admin
QBITTORRENT_PASS=xxx

# PrivateHD
PRIVATEHD_RSS=https://privatehd.to/rss/torrents/movie?pid=xxx

# Immich
IMMICH_URL=https://photos.sam9scloud.in
IMMICH_API_KEY=xxx

# TMDB
TMDB_API_KEY=xxx

# AzuraCast
AZURACAST_URL=https://radio.sam9scloud.in
AZURACAST_API_KEY=xxx

# OpenClaw Bridge
OPENCLAW_WEBHOOK_SECRET=xxx
```

---

## **9. SUCCESS METRICS**

| Metric | Target |
|--------|--------|
| Query response time | < 2s |
| Torrent add success | > 95% |
| Photo search accuracy | > 90% |
| Uptime | > 99% |
| User satisfaction | "Just works" |

---

## **10. DECISIONS TO MAKE**

1. **Repository:** Create `github.com/sam9s/media-assistant` ?
2. **Database:** SQLite (simple) or PostgreSQL (scalable) ?
3. **LLM for Recommendations:** Claude API or local model ?
4. **OpenClaw Channel:** Telegram, WhatsApp, or both ?
5. **Voice Support:** Yes/No (adds complexity)

---

**Next Step:** Your review and approval → Create repository → Begin Phase 1
