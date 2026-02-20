# VPS Media Server Infrastructure Audit

**Server:** sam9scloud.in  
**Orchestration:** Dokploy (Docker-based PaaS)  
**Reverse Proxy:** Traefik (Dokploy-managed) + Caddy  
**Date Audited:** 2026-02-19

---

## **1. MEDIA SERVICES**

| Service | Container | Port | Domain | API Available | Purpose |
|---------|-----------|------|--------|---------------|---------|
| **Jellyfin** | sam9scloud-jellyfin-k27hvc-jellyfin-1 | 8096 | movies.sam9scloud.in | ✅ Yes | Movies/TV streaming |
| **Jellystat** | jellystat | 3000 | jellystat.sam9scloud.in | ✅ Yes | Jellyfin analytics |
| **Kavita** | sam9scloud-kavita-t7ylne-kavita-1 | 5000 | reader.sam9scloud.in | ✅ Yes | eBooks/Comics |
| **Audiobookshelf** | sam9scloud-audiobookshelf-1v1kqz | - | abs.sam9scloud.in | ✅ Yes | Audiobooks |
| **Immich** | sam9scloud-immich-et3bzi | - | photos.sam9scloud.in | ✅ Yes | Photo management |
| **AzuraCast** | azuracast | 8000 | radio.sam9scloud.in | ✅ Yes | Radio streaming |
| **Navidrome** | sam9scloud-navidome-8ympaj | - | music.sam9scloud.in | ✅ Yes | Music streaming |
| **Airsonic** | sam9scloud-airsonic-qincze | - | - | ✅ Yes | Music (legacy) |

---

## **2. DOWNLOAD & FILE MANAGEMENT**

| Service | Container | Port | Domain | API Available | Purpose |
|---------|-----------|------|--------|---------------|---------|
| **qBittorrent** | sam9scloud-qbittorrent-vpexci-qbittorrent-1 | 8080 | downloads.sam9scloud.in | ✅ Yes | Torrent client |
| **qB-shim** | qb-shim | 8088 | - | ✅ Custom | API wrapper for qBittorrent |
| **Filebrowser** | sam9scloud-filebrowser-ubme3u-filebrowser-1 | 80 | files.sam9scloud.in | ✅ Yes | File manager |

---

## **3. DOCUMENTS & DATA**

| Service | Container | Port | Domain | API Available | Purpose |
|---------|-----------|------|--------|---------------|---------|
| **Paperless-ngx** | sam9scloud-paperlessngx-v00mni | - | docs.sam9scloud.in | ✅ Yes | Document management |
| **Homebox** | sam9scloud-homebox-yo7eel | - | homebox.sam9scloud.in | ✅ Yes | Home inventory |

---

## **4. MONITORING & AUTOMATION**

| Service | Container | Port | Domain | API Available | Purpose |
|---------|-----------|------|--------|---------------|---------|
| **Grafana** | grafana | 3000 | - | ✅ Yes | Metrics visualization |
| **Prometheus** | sam9scloud-prometheus-amhsw0 | - | - | ✅ Yes | Metrics collection |
| **Uptime Kuma** | sam9scloud-uptimekuma-u4orys | - | - | ✅ Yes | Uptime monitoring |
| **n8n** | n8n | 5678 | - | ✅ Yes | Workflow automation |
| **Jellystat** | jellystat | 3000 | jellystat.sam9scloud.in | ✅ Yes | Jellyfin stats |

---

## **5. AI & ASSISTANTS**

| Service | Container | Port | Domain | API Available | Purpose |
|---------|-----------|------|--------|---------------|---------|
| **OpenClaw** | clawwork-sandbox-clawwork-dashboard-1 | 9000 | - | N/A | Autonomous AI assistant |
| **AnythingLLM** | anythingllm | 3001 | - | ✅ Yes | RAG/Chat interface |

---

## **6. DASHBOARDS**

| Service | Container | Port | Domain | Status |
|---------|-----------|------|--------|--------|
| **Homepage** | sam9scloud-homepage-ykw9xg | - | - | Running |
| **Homarr** | sam9scloud-homarr-kqn7yq | - | - | Running |
| **Dashy** | sam9scloud-dashy-bplvb0 | - | - | Running |
| **RamenUI** | sam9scloud-ramenui-flreps | - | - | Running (Custom) |

---

## **7. EXTERNAL INTEGRATIONS**

| Service | Type | RSS/API | Purpose |
|---------|------|---------|---------|
| **PrivateHD** | Tracker | RSS | Movie torrents |
| **Trakt.tv** | Service | API | Watch history |
| **TMDB** | Service | API | Movie metadata/posters |
| **IMDb** | Service | Import | Ratings |

---

## **8. KNOWN API ENDPOINTS**

```
# Jellyfin
https://movies.sam9scloud.in/
API Key: d5c97c8f30f1418a9573f8806b8ea334

# qBittorrent (via shim)
http://localhost:8088/api/qbittorrent/stats

# AzuraCast
https://radio.sam9scloud.in/api/nowplaying

# PrivateHD RSS
https://privatehd.to/rss/torrents/movie?pid=91d7d103aa13829d60920bda213f956f

# TMDB
https://api.themoviedb.org/3/
API Key: 0022c77a66930474249f273d4d79457b
```

---

## **9. DATA LOCATIONS**

```
/media/          # Main media storage
/config/         # Application configs
/root/           # Docker compose files
/etc/dokploy/    # Dokploy managed services
```

---

## **10. NETWORK ARCHITECTURE**

```
Internet → Caddy/Traefik (443/80) → Docker Services
                               ↓
                        Dokploy Management (3000)
```

**SSL:** Managed by Dokploy/Let's Encrypt  
**DNS:** Cloudflare (assumed based on domain pattern)
