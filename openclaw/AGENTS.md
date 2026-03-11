# Agents / Routing Rules

This file defines which skill should handle which request.

## Primary rule

If a request clearly matches a skill, use that skill.

If a request spans multiple media domains or needs orchestration across pipelines, use `media-manager`.

If a request is ambiguous and more than one interpretation is plausible, ask Sam one short clarifying question.

If a request is risky, destructive, or operationally disruptive, ask Sam before doing anything.

## Routing

| Intent | Skill |
|---|---|
| Search for a movie or TV show | `media-assistant` |
| Download a movie or TV show | `media-assistant` |
| Check movie/TV download progress or library status | `media-assistant` |
| Search or manage subtitles for a movie | `media-assistant` |
| Ask about qBittorrent, Jellyfin, or movie pipeline state | `media-assistant` |
| Search for a FLAC album, artist, or song | `music` |
| Download FLAC music | `music` |
| Import local FLAC files/folders into the music library | `music` |
| Check Navidrome music library or music enrichment status | `music` |
| Search YouTube music or download from YouTube | `youtube` |
| Ask about YouTube_Music library output or YouTube audio status | `youtube` |
| Search for a book, novel, comic, or magazine | `librarian` |
| Download a book, novel, comic, or magazine | `librarian` |
| Check Kavita library or librarian status | `librarian` |
| Ask for recommendations, weekly discovery, or cross-media suggestions | `recommendations` |
| Ask what to watch, hear, or read next | `recommendations` |
| Upload a radio track or ask what the station is playing | `radio` |
| Bulk import mixed local audio/video files into radio | `radio-audio-import` |
| Upload a photo, create/manage albums, search or download Immich assets | `photos` |
| Ask for VPS health, service state, container state, disk, CPU, RAM | `vps-health` |
| Ask for top-level media server management, cross-pipeline coordination, targeted cleanup, validation, or "handle this for me" across domains | `media-manager` |

## Default

Requests that do not clearly match a skill are handled by the base model directly.

## Operating notes

- Movies and TV -> `media-assistant`
- Music / Navidrome / FLAC import -> `music`
- YouTube audio -> `youtube`
- Books / Kavita -> `librarian`
- Radio / AzuraCast -> `radio` or `radio-audio-import`
- Photos / Immich -> `photos`
- Recommendations / discovery -> `recommendations`
- VPS ops / health -> `vps-health`
- Multi-step or cross-domain media tasks -> `media-manager`

## Safety notes

- Do not improvise destructive actions.
- Do not restart services, delete content, or perform broad cleanup without explicit approval.
- When safe, prefer targeted actions over full reruns.
