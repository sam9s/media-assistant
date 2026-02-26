#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  BACKUP_PASSPHRASE='strong-passphrase' ./scripts/backup_config_bundle.sh [--dry-run]

Optional env vars:
  DEST_DIR       Backup destination (default: /mnt/cloud/gdrive/Backups/sam-media-assistant-config)
  BACKUP_LABEL   Prefix for archive filenames (default: sam-media-assistant-config)
  WORK_DIR       Local temp directory for build artifacts (default: /tmp)

Notes:
  - This script backs up configuration and secrets only (no media/download data).
  - Output archive is AES-256 encrypted via OpenSSL (PBKDF2).
EOF
}

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ $# -gt 0 ]]; then
  echo "Unknown argument: $1" >&2
  usage
  exit 1
fi

BACKUP_LABEL="${BACKUP_LABEL:-sam-media-assistant-config}"
DEST_DIR="${DEST_DIR:-/mnt/cloud/gdrive/Backups/${BACKUP_LABEL}}"
WORK_DIR="${WORK_DIR:-/tmp}"
TIMESTAMP_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
HOSTNAME_SHORT="$(hostname -s)"

BASE_NAME="${BACKUP_LABEL}_${HOSTNAME_SHORT}_${TIMESTAMP_UTC}"
RAW_ARCHIVE="${WORK_DIR}/${BASE_NAME}.tar.gz"
ENC_ARCHIVE="${RAW_ARCHIVE}.enc"
META_FILE="${WORK_DIR}/${BASE_NAME}.metadata.txt"
SHA_FILE="${ENC_ARCHIVE}.sha256"

declare -a CANDIDATE_PATHS=(
  "root/apps/sam-media-api"
  "opt/dokploy/volumes/qbittorrent/config"
  "var/lib/docker/volumes/sam-media-api_jackett_config/_data"
  "opt/dokploy/volumes/jellyfin/config"
  "root/.config/rclone/rclone.conf"
)

declare -a EXISTING_PATHS=()
for rel in "${CANDIDATE_PATHS[@]}"; do
  if [[ -e "/${rel}" ]]; then
    EXISTING_PATHS+=("${rel}")
  else
    echo "WARN: skipping missing path: /${rel}" >&2
  fi
done

if [[ ${#EXISTING_PATHS[@]} -eq 0 ]]; then
  echo "ERROR: no backup paths found." >&2
  exit 1
fi

echo "Backup destination: ${DEST_DIR}"
echo "Backup items:"
for rel in "${EXISTING_PATHS[@]}"; do
  echo "  - /${rel}"
done

if [[ ${DRY_RUN} -eq 1 ]]; then
  echo "Dry run complete. No archive created."
  exit 0
fi

if [[ -z "${BACKUP_PASSPHRASE:-}" ]]; then
  echo "ERROR: BACKUP_PASSPHRASE is required for encryption." >&2
  exit 1
fi

mkdir -p "${DEST_DIR}"

{
  echo "timestamp_utc=${TIMESTAMP_UTC}"
  echo "hostname=${HOSTNAME_SHORT}"
  echo "backup_label=${BACKUP_LABEL}"
  echo "dest_dir=${DEST_DIR}"
  echo "included_paths:"
  for rel in "${EXISTING_PATHS[@]}"; do
    echo "  - /${rel}"
  done
  echo "qbt_key_settings:"
  QBT_CONF="/opt/dokploy/volumes/qbittorrent/config/qBittorrent/qBittorrent.conf"
  if [[ -f "${QBT_CONF}" ]]; then
    grep -E '^\[AutoRun\]|^enabled=|^program=|^Session\\QueueingSystemEnabled=|^Session\\MaxActive' "${QBT_CONF}" || true
  else
    echo "  qBittorrent.conf not found"
  fi
} > "${META_FILE}"

tar -C / -czf "${RAW_ARCHIVE}" "${EXISTING_PATHS[@]}"
openssl enc -aes-256-cbc -pbkdf2 -salt \
  -in "${RAW_ARCHIVE}" \
  -out "${ENC_ARCHIVE}" \
  -pass env:BACKUP_PASSPHRASE
# Write checksum with archive basename so verification works from destination dir.
(cd "$(dirname "${ENC_ARCHIVE}")" && sha256sum "$(basename "${ENC_ARCHIVE}")") > "${SHA_FILE}"

cp -f "${ENC_ARCHIVE}" "${DEST_DIR}/"
cp -f "${SHA_FILE}" "${DEST_DIR}/"
cp -f "${META_FILE}" "${DEST_DIR}/"

rm -f "${RAW_ARCHIVE}" "${ENC_ARCHIVE}" "${SHA_FILE}" "${META_FILE}"

echo "Backup created successfully:"
echo "  - ${DEST_DIR}/$(basename "${ENC_ARCHIVE}")"
echo "  - ${DEST_DIR}/$(basename "${SHA_FILE}")"
echo "  - ${DEST_DIR}/$(basename "${BASE_NAME}.metadata.txt")"
