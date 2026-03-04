#!/usr/bin/env bash
# Run on VPS: calls sam-media-api (localhost:8765) with API_KEY from .env.
# Usage: ./call-api.sh search "query"
#        ./call-api.sh download <torrent_url> <title> <year> [category]
#        ./call-api.sh health
set -e
REPO="${SAM_MEDIA_REPO:-/root/apps/sam-media-api}"
cd "$REPO"
if [[ ! -f "$REPO/.env" ]]; then
  echo "Missing $REPO/.env"
  exit 1
fi
API_KEY=$(grep '^API_KEY=' "$REPO/.env" | cut -d= -f2- | tr -d '\r"' | head -1)
if [[ -z "$API_KEY" ]]; then
  echo "API_KEY not set in .env"
  exit 1
fi
BASE="http://localhost:8765"

case "$1" in
  search)
    QUERY="${2:-}"
    if [[ -z "$QUERY" ]]; then
      echo "Usage: $0 search \"query\""
      exit 1
    fi
    # Escape double quotes for JSON
    Q="${QUERY//\"/\\\"}"
    curl -s -X POST "$BASE/search" \
      -H "X-API-Key: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"query\":\"$Q\",\"limit\":5}"
    ;;
  download)
    URL="$2"
    TITLE="$3"
    YEAR="$4"
    CATEGORY="${5:-hollywood}"
    if [[ -z "$URL" || -z "$TITLE" ]]; then
      echo "Usage: $0 download <torrent_url> <title> <year> [category]"
      exit 1
    fi
    T="${TITLE//\"/\\\"}"
    curl -s -X POST "$BASE/download" \
      -H "X-API-Key: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"torrent_url\":\"$URL\",\"title\":\"$T\",\"year\":\"$YEAR\",\"category\":\"$CATEGORY\"}"
    ;;
  health)
    curl -s "$BASE/health"
    ;;
  complete)
    # Manual trigger for /complete (e.g. Option B: re-run pipeline for already-downloaded torrent)
    # Usage: $0 complete "Name|Year" <category> <content_path> [info_hash]
    NAME="${2:-}"
    CATEGORY="${3:-}"
    CONTENT_PATH="${4:-}"
    INFO_HASH="${5:-0000000000000000000000000000000000000000}"
    if [[ -z "$NAME" || -z "$CATEGORY" || -z "$CONTENT_PATH" ]]; then
      echo "Usage: $0 complete \"Name|Year\" <category> <content_path> [info_hash]"
      exit 1
    fi
    N="${NAME//\"/\\\"}"
    P="${CONTENT_PATH//\"/\\\"}"
    curl -s -X POST "$BASE/complete" \
      -H "X-API-Key: $API_KEY" \
      -H "Content-Type: application/json" \
      -d "{\"name\":\"$N\",\"category\":\"$CATEGORY\",\"content_path\":\"$P\",\"info_hash\":\"$INFO_HASH\"}"
    ;;
  *)
    echo "Usage: $0 search \"query\" | download <url> <title> <year> [category] | health | complete \"Name|Year\" <category> <content_path> [info_hash]"
    exit 1
    ;;
esac
