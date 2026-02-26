# Disaster Recovery Runbook (Config-Only Backup)

## Goal
Backup and restore the full media automation configuration (without large media data) so the system can be rebuilt on a new VPS.

## Scope (What Is Backed Up)
- `/root/apps/sam-media-api` (code + `.env` + deployment files)
- `/opt/dokploy/volumes/qbittorrent/config` (qBittorrent UI/API settings, AutoRun webhook, session config)
- `/var/lib/docker/volumes/sam-media-api_jackett_config/_data` (Jackett settings/indexers)
- `/opt/dokploy/volumes/jellyfin/config` (Jellyfin server/library config)
- `/root/.config/rclone/rclone.conf` (rclone remote config)

## Scope (What Is Not Backed Up)
- `/srv/downloads` media payload (intentionally excluded due to size)
- Google Drive media content (`/mnt/cloud/gdrive/Media`) since it already lives in cloud storage

## Why This Covers Automation
The full pipeline automation state is stored in config:
- qB completion webhook command is in `qBittorrent.conf` (`[AutoRun] program=.../complete...`)
- qB queueing/seeding limits are in `qBittorrent.conf`
- API behavior is in repository code (`app/main.py`) and `.env`
- Jellyfin refresh is called by API `/complete` endpoint (code), not by a separate Jellyfin toggle

## Backup Location Strategy
- Primary backup target: off-host Google Drive mount (`/mnt/cloud/gdrive/Backups/...`)
- Keep encrypted archives only
- Keep encryption passphrase outside the VPS (password manager)

## Backup Command
From `/root/apps/sam-media-api`:

```bash
chmod +x scripts/backup_config_bundle.sh
BACKUP_PASSPHRASE='<strong-passphrase>' ./scripts/backup_config_bundle.sh
```

Default destination:

```text
/mnt/cloud/gdrive/Backups/sam-media-assistant-config
```

Optional destination override:

```bash
DEST_DIR='/mnt/cloud/gdrive/Backups/custom-folder' \
BACKUP_PASSPHRASE='<strong-passphrase>' \
./scripts/backup_config_bundle.sh
```

Dry run:

```bash
./scripts/backup_config_bundle.sh --dry-run
```

## Backup Output
For each run, three files are generated:
- `*.tar.gz.enc` (encrypted archive)
- `*.tar.gz.enc.sha256` (checksum)
- `*.metadata.txt` (included paths + key qB settings snapshot)

## Restore on New VPS
1. Prepare base server:
- Install Docker/Compose
- Create required mount points (`/srv/downloads`, `/mnt/cloud/gdrive`, etc.)
- Ensure rclone mount is available if using same Google Drive pattern

2. Get backup files from storage:
- Copy `.enc`, `.sha256`, `.metadata.txt` to local temp directory

3. Verify checksum:

```bash
sha256sum -c sam-media-assistant-config_<host>_<timestamp>.tar.gz.enc.sha256
```

4. Decrypt archive:

```bash
export BACKUP_PASSPHRASE='<strong-passphrase>'
openssl enc -d -aes-256-cbc -pbkdf2 -salt \
  -in sam-media-assistant-config_<host>_<timestamp>.tar.gz.enc \
  -out restore.tar.gz \
  -pass env:BACKUP_PASSPHRASE
```

5. Stop related containers before restore:

```bash
docker stop sam-media-api jackett sam9scloud-qbittorrent-vpexci-qbittorrent-1 sam9scloud-jellyfin-k27hvc-jellyfin-1 || true
```

6. Restore files to root:

```bash
tar -C / -xzf restore.tar.gz
```

7. Start/recreate services:

```bash
cd /root/apps/sam-media-api
docker compose up -d
# Start qB/Jellyfin stacks from Dokploy or existing compose deployment
```

8. Validate critical behavior:
- API health:
```bash
curl http://localhost:8765/health
```
- qB webhook configured:
```bash
rg -n "AutoRun|program=" /opt/dokploy/volumes/qbittorrent/config/qBittorrent/qBittorrent.conf
```
- qB queueing/seeding limits:
```bash
rg -n "QueueingSystemEnabled|MaxActive" /opt/dokploy/volumes/qbittorrent/config/qBittorrent/qBittorrent.conf
```
- Run one end-to-end torrent test

## Recommended Schedule
- Daily config backup (script above) via cron/systemd timer
- Keep 30-60 days of encrypted snapshots

## Security Notes
- Never store passphrase in plaintext on VPS
- Treat `.env`, qB config, and rclone config as sensitive
- Rotate keys immediately if leaked

