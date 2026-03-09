# AzuraCast Radio Status

## Current live state

- Service: `AzuraCast 0.22.1`
- Domain: `https://radio.sam9scloud.in`
- Station ID: `2`
- Station shortcode: `sam9s.radio`
- Public stream URL: `https://radio.sam9scloud.in/listen/sam9s.radio/radio.mp3`

## What was implemented

- The canonical host-side radio library is now:
  - `/mnt/cloud/gdrive/Media/Radio`
- AzuraCast now mounts that host path directly as the station media path:
  - host: `/mnt/cloud/gdrive/Media/Radio`
  - container: `/var/azuracast/stations/sam9sradio/media`
- The AzuraCast `storage_location` entry for station media was updated from `/ext-media` to `/var/azuracast/stations/sam9sradio/media`.
- Existing live station media records were preserved and now resolve correctly from the shared library path.
- The station is playing normally again from the migrated library.

## Agreed direction

- Radio should use one shared folder, not separate language folders.
- Target canonical library path:
  - `/mnt/cloud/gdrive/Media/Radio`
- Phase 1:
  - migrate existing station media into `/mnt/cloud/gdrive/Media/Radio`
  - make AzuraCast use that folder as the station media path without breaking the existing random-play station
- Phase 2:
  - add API automation to upload MP3s into the radio folder
  - expose `nowplaying` through `sam-media-api`
- Phase 3:
  - DJ drops / liners / rotation rules
  - requires separate planning

## Useful live commands discovered

- Reprocess station media:
  - `docker exec azuracast php /var/azuracast/www/backend/bin/console azuracast:media:reprocess sam9s.radio <path>`
- Restart station:
  - `docker exec azuracast php /var/azuracast/www/backend/bin/console azuracast:radio:restart sam9s.radio`

## Current implementation boundary

- `sam-media-api` now has:
  - `GET /radio/nowplaying`
  - `POST /radio/upload`
- Upload uses AzuraCast's native media upload API and lands in the station media library.
- Automated uploads currently target:
  - `/mnt/cloud/gdrive/Media/Radio/Hindi`
- A separate batch-import workflow now exists for mixed local folders:
  - skill: `skills/radio-audio-import/SKILL.md`
  - script: `skills/radio-audio-import/scripts/import_to_radio.py`
  - behavior: recurse, skip FLAC, convert `.mp4` / `.webm` to temporary MP3, upload via `/radio/upload`
- Phase 1 is implemented and validated.
- Phase 2 is implemented and validated.
- Phase 3 remains pending:
  - DJ drops / liners / rotation rules

## Validation completed

- `GET /radio/nowplaying` returns live station metadata and current song.
- Station playback recovered after migration and no longer falls back to the error track.
- `POST /radio/upload` was tested and confirmed to:
  - upload via native AzuraCast API
  - index immediately in AzuraCast media
  - return the created AzuraCast file ID/path
- Recursive mixed-folder import was validated against:
  - `D:\Softwares_Apps\Entertainment\MUSIC`
  - Result: `17` eligible files imported successfully (`2` direct MP3, `15` converted from MP4/WEBM), `0` failures, `539` FLAC files skipped
- Temporary migration/test duplicates were removed from both the folder and AzuraCast DB after validation.
