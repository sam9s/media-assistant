# Agents / Routing Rules

This file defines which skill handles which type of user request.

## Routing

| Intent | Skill |
|---|---|
| Search for a movie or TV show | `media-assistant` |
| Download a movie, show, or music | `media-assistant` |
| Check download progress or status | `media-assistant` |
| Check if something is in the Jellyfin library | `media-assistant` |
| Add a torrent | `media-assistant` |
| Ask about qBittorrent or Jellyfin | `media-assistant` |

## Default

All requests that don't match a specific skill are handled by the base model directly (no skill invoked).

## Notes

- The `media-assistant` skill is the only skill currently installed.
- Additional skills (e.g., home automation, news) can be added here as new entries when installed.
- When in doubt about intent, ask the user one short clarifying question before routing.
