#!/usr/bin/env bash
set -euo pipefail
cd /root/apps/sam-media-api
API_KEY=$(grep '^API_KEY=' .env | head -n1 | cut -d= -f2-)
curl -fsS -X POST http://127.0.0.1:8765/recommendations/generate \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"mode":"weekly","lookback_days":7,"library_only":false,"include_new_finds":true,"limit_per_type":1}'
