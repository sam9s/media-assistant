# Heartbeat

This file defines what Raven does on scheduled health pulses — proactive monitoring without Sam having to ask.

## Schedule

- **Interval**: Every 15 minutes (configurable)
- **On startup**: Run one full health check immediately after Raven boots

## What to check on every heartbeat

### 1. Docker Container Health
Run: `docker ps -a --format '{{.Names}}|{{.Status}}|{{.RunningFor}}'`

Flag any container that is:
- **Stopped / Exited** — unless it's a known stopped container (see Ignored list below)
- **Restarting** — in a restart loop (status contains "Restarting")
- **Unhealthy** — health check is failing (status contains "unhealthy")
- **Dead** — container is dead

### 2. System Resource Check
Run: `free -h` and `df -h` and `top -bn1`

Flag if:
- Available RAM is **below 500 MB**
- Any disk/volume is **above 85% full**
- Any single process is consuming **more than 80% CPU** sustained

### 3. Media API Liveness
Run: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8765/health`

Flag if response is not `200`.

### 4. rclone FUSE Mount
Run: `ls /mnt/cloud/gdrive/ > /dev/null 2>&1 && echo ok || echo fail`

Flag if mount is not accessible (would break all Google Drive + Jellyfin writes).

## Alert format (sent to Sam on Telegram)

Only alert if something is wrong. Do NOT send "all clear" messages on every heartbeat — that is noise.

```
🚨 VPS Alert — [timestamp]

❌ Containers down: [list]
⚠️ Restart loop: [list]
💾 Disk: /dev/sda1 at 91% — action needed
🧠 RAM critical: only 312 MB free
📡 Media API: unhealthy

Use /health for full report.
```

If multiple issues, group them into one message. Never send a separate Telegram message per issue.

## Ignored containers (known stopped — do not alert)

These containers are intentionally stopped and should not trigger alerts:

- `ramen-ui` — stopped by design (static build, served differently)
- `clawwork-sandbox-frontend-1` — dev sandbox, not in active use
- `clawwork-sandbox-backend-1` — dev sandbox, not in active use
- `clawwork-sandbox-clawwork-agent-1` — dev sandbox, not in active use
- `dokploy.1.le5fuw343ilgrk1an53uofjd1` — old Swarm replica, replaced
- `dokploy-redis.1.zerlnx0w248f478izku6599rp` — old Swarm replica, replaced
- `dokploy-postgres.1.bdze0g75se77uu4n9mb96g55s` — old Swarm replica, replaced

## On-demand health report

When Sam asks "is everything okay?" or "check VPS health" — invoke the `vps-health` skill for a full interactive report. The heartbeat is background-only; the skill handles direct queries.
