---
name: recommendations
description: Cross-media recommendation engine for Raven. Use when Sam asks for recommendations, weekly discovery, theme-based suggestions, or discovery across movies, music, and books.
metadata: {"openclaw":{"requires":{"env":["MEDIA_API_URL","MEDIA_API_KEY"]},"primaryEnv":"MEDIA_API_KEY"}}
---

# Recommendations Skill

Use this skill when Sam asks for:
- weekly discovery
- recommend something
- recommend a movie / book / album / track
- cross-media suggestions from recent activity
- discovery linked by theme, creator, soundtrack, lore, poetry, or mood

This skill is suggest-only in phase 1.
It does not auto-download anything.
If Sam wants acquisition after a recommendation, hand off to `media-manager`, which can route to the appropriate pipeline.

## Endpoints

Base URL: `${MEDIA_API_URL}`
Header: `X-API-Key: ${MEDIA_API_KEY}`

### Health
- `GET /recommendations/health`

### Manual ingestion refresh
- `POST /recommendations/ingest/run`

Example body:
```json
{
  "sources": ["jellyfin", "navidrome", "kavita"],
  "window_days": 30,
  "rebuild_signals": true
}
```

### Ingestion status
- `GET /recommendations/ingest/status`

### Generate recommendations
- `POST /recommendations/generate`

Example weekly body:
```json
{
  "mode": "weekly",
  "lookback_days": 7,
  "library_only": false,
  "include_new_finds": true,
  "limit_per_type": 1
}
```

### Latest weekly digest
- `GET /recommendations/latest`

### Theme or seed query
- `POST /recommendations/query`

Example body:
```json
{
  "seed_type": "theme",
  "seed_value": "Umrao Jaan",
  "include_library": true,
  "include_external": true
}
```

### History
- `GET /recommendations/history`

## Operational Rules

1. If the recommendation sounds broad or weekly, generate or fetch the digest.
2. If Sam names a title, person, or theme, use `/recommendations/query`.
3. If Sam wants to act on a recommendation, stop using this skill and hand off to `media-manager`.
4. Do not present recommendations as facts. Present them as engine suggestions with the provided rationale.
5. Mention whether the item is already in the library and which pipeline would acquire it if not.

## Current Phase 1 Scope

- Sources: Jellyfin, Navidrome, Kavita
- Storage: Postgres
- Delivery: API only
- Telegram send is deferred until OpenClaw is deployed on VPS
- LLM synthesis is optional and depends on `OPENROUTER_API_KEY`
- External candidate enrichment is optional and depends on TMDB / Last.fm / Open Library availability
