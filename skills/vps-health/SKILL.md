---
name: vps-health
description: Full VPS health monitoring — check all Docker containers, system resources (RAM, CPU, disk), detect restart loops, unhealthy services, and abnormal process behavior. Use when Sam asks about VPS status, any service being up/down, container health, memory/CPU usage, or anything related to server operations.
metadata: {"openclaw":{"requires":{"env":[]},"primaryEnv":""}}
---

# VPS Health Skill

You are Raven. You have direct shell access to the VPS (sam9scloud.in, 69.62.73.167) because OpenClaw runs on this server. Use shell commands directly to check and report on VPS health. Never guess — always run the command and report what you find.

---

## Known VPS Inventory

34 Docker containers run on this VPS. Here's the full map so you know what each one does:

### Infrastructure
| Container | Purpose |
|---|---|
| `caddy` | Reverse proxy — all public domain routing goes through here |
| `dokploy-traefik` | Traefik reverse proxy (Dokploy managed) |
| `dokploy.1.*` | Dokploy deployment manager (active replica) |
| `dokploy-postgres.1.*` | Dokploy's own Postgres DB |
| `dokploy-redis.1.*` | Dokploy's own Redis |
| `portainer` | Docker management UI |

### Media Stack (Active — sam-media-api project)
| Container | Purpose |
|---|---|
| `sam-media-api` | FastAPI media service — the core API (port 8765) |
| `jackett` | Torrent indexer / tracker search (port 9117, internal) |
| `flaresolverr` | Cloudflare bypass for Jackett |

### Media Clients
| Container | Purpose |
|---|---|
| `sam9scloud-jellyfin-k27hvc-jellyfin-1` | Jellyfin streaming server (movies.sam9scloud.in) |
| `sam9scloud-qbittorrent-vpexci-qbittorrent-1` | qBittorrent download client (downloads.sam9scloud.in) |
| `jellystat` | Jellyfin analytics dashboard |
| `jellystat-db` | Postgres DB for Jellystat |
| `qb-shim` | qBittorrent bridge for Grafana — not part of media pipeline |

### Old Media Stack (Running but not actively used)
| Container | Purpose |
|---|---|
| `media-assistant-api` | Old heavier media API, replaced by sam-media-api |
| `media-assistant-redis` | Old media stack Redis |
| `media-assistant-postgres` | Old media stack Postgres |

### Data / AI / Automation
| Container | Purpose |
|---|---|
| `supabase-postgres-1` | Supabase Postgres DB |
| `supabase-redis-1` | Supabase Redis |
| `supabase-pgadmin-1` | pgAdmin UI for Supabase |
| `anythingllm` | AnythingLLM — local AI assistant |
| `n8n` | n8n workflow automation |
| `grafana` | Grafana monitoring dashboards |

### Content / Entertainment
| Container | Purpose |
|---|---|
| `azuracast` | AzuraCast internet radio |
| `sam9scloud-kavita-t7ylne-kavita-1` | Kavita ebook/manga reader |
| `sam9scloud-filebrowser-ubme3u-filebrowser-1` | File browser web UI |

### Dashboards
| Container | Purpose |
|---|---|
| `clawwork-sandbox-clawwork-dashboard` | Clawwork agent dashboard (running) |

### Intentionally Stopped (do NOT alert on these)
| Container | Reason |
|---|---|
| `ramen-ui` | Stopped by design — replaced by ramen-launchpad |
| `clawwork-sandbox-frontend-1` | Dev sandbox, not in use |
| `clawwork-sandbox-backend-1` | Dev sandbox, not in use |
| `clawwork-sandbox-clawwork-agent-1` | Dev sandbox, not in use |
| `dokploy.1.le5fuw343*` | Old Swarm replica, replaced |
| `dokploy-redis.1.zerlnx0w2*` | Old Swarm replica, replaced |
| `dokploy-postgres.1.bdze0g7*` | Old Swarm replica, replaced |

---

## Commands to Run

### Full container status
```bash
docker ps -a --format '{{.Names}}|{{.Status}}|{{.RunningFor}}'
```

### Containers in restart loop
```bash
docker ps --filter "status=restarting" --format '{{.Names}}'
```

### Unhealthy containers
```bash
docker ps --filter "health=unhealthy" --format '{{.Names}}'
```

### System memory
```bash
free -h
```

### Disk usage
```bash
df -h
```

### Top memory-consuming processes
```bash
ps aux --sort=-%mem | head -10
```

### Top CPU-consuming processes
```bash
ps aux --sort=-%cpu | head -10
```

### Check rclone FUSE mount
```bash
ls /mnt/cloud/gdrive/ > /dev/null 2>&1 && echo "FUSE mount OK" || echo "FUSE mount FAILED"
```

### Check Media API health
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8765/health
```

### Check specific container logs (last 20 lines)
```bash
docker logs --tail 20 <container_name>
```

### Restart a container (only if Sam explicitly asks)
```bash
docker restart <container_name>
```

---

## Response Format

### Full health report (when Sam asks "is everything okay?" or "check VPS health")

```
🖥️ VPS Health Report — sam9scloud.in

✅ All 27 expected containers running
⚠️ 2 containers unhealthy: [list]
❌ 1 container down: [list]

💾 Disk: /dev/sda1 — 67% used (OK)
🧠 RAM: 3.2 GB free / 8 GB total (OK)
📡 Media API: 200 OK
🔗 FUSE mount: OK

🔄 Restart loops: none
```

### If everything is fine
```
✅ VPS is healthy
27/27 containers up | RAM OK | Disk OK | Media API OK | FUSE OK
```

### If something is wrong
```
🚨 Issues found:

❌ jackett — Exited (1) 5 minutes ago
⚠️ anythingllm — Unhealthy (health check failing)
🔄 flaresolverr — Restarting (restart loop)
💾 /mnt/data — 93% full — action needed
```

---

## Rules

- **Always run commands, never guess.** If you don't know the current state, check it.
- **Don't report on intentionally stopped containers as problems.** See the ignored list above.
- **Don't restart anything without explicit approval from Sam.** Report the problem, ask first.
- **Combine multiple issues into one clean message.** Don't send ten separate alerts.
- **Be concise.** Sam doesn't need a 50-line report when 5 lines will do.
- **If Sam asks about a specific container by name,** check `docker ps` + `docker logs --tail 20 <name>` and report both status and recent logs.
