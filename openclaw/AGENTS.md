# Agents / Routing Rules

This file defines which skill handles which type of user request.

## Routing

| Intent | Skill |
|---|---|
| Search for a movie or TV show | `media-assistant` |
| Download a movie or TV show | `media-assistant` |
| Check movie/TV download progress or status | `media-assistant` |
| Check if something is in the Jellyfin library | `media-assistant` |
| Add a torrent | `media-assistant` |
| Ask about qBittorrent or Jellyfin | `media-assistant` |
| Search for a music album or artist in FLAC | `music` |
| Download a music album (FLAC, lossless) | `music` |
| Check music download or enrichment status | `music` |
| Check if an album is in Navidrome | `music` |
| Ask about Navidrome or music library | `music` |
| Search for a book, novel, or ebook | `librarian` |
| Download a book, novel, or ebook | `librarian` |
| Search for a comic or graphic novel | `librarian` |
| Download a comic or graphic novel | `librarian` |
| Search for a magazine | `librarian` |
| Download a magazine | `librarian` |
| Check if a book is in Kavita library | `librarian` |
| Ask about Kavita or book library | `librarian` |
| Check VPS health or container status | `vps-health` |
| Report system resource usage | `vps-health` |
| Alert about a failed or crashed container | `vps-health` |

## Default

All requests that don't match a specific skill are handled by the base model directly (no skill invoked).

## Notes

- When in doubt about intent, ask the user one short clarifying question before routing.
- Books, comics, and magazines → `librarian` skill (Kavita pipeline)
- Movies and TV shows → `media-assistant` skill (qBittorrent + Jellyfin pipeline)
- Music albums (FLAC) → `music` skill (Soulseek/slskd + Navidrome pipeline)

