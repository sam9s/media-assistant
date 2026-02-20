# Media Assistant API

Hybrid AI Media Manager for Sam's Cloud Server

## Architecture

- **Media Assistant API**: FastAPI backend (no LLM censorship)
- **OpenClaw**: Conversational interface
- **Supabase**: PostgreSQL database
- **Radarr/Lidarr**: Media management
- **Portainer**: Docker management

## Directory Structure

- `docs/`: Documentation
- `src/api/`: FastAPI application
- `src/workers/`: Background job workers
- `src/clients/`: Service clients (Jellyfin, qBittorrent, etc.)
- `supabase/`: Supabase Docker deployment
- `scripts/`: Utility scripts
- `tests/`: Test suite
- `config/`: Configuration files

## Quick Start

```bash
# Start Supabase
cd supabase && docker-compose up -d

# Start API
cd src/api && uvicorn main:app --reload
```
