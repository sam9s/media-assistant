---
name: youtube
description: Search YouTube (playlist-first), let Sam choose, then download audio into language-specific YouTube_Music folders for Navidrome. Use when Sam asks to search or download from YouTube.
metadata: {"openclaw":{"requires":{"env":["MEDIA_API_URL","MEDIA_API_KEY"]},"primaryEnv":"MEDIA_API_KEY"}}
---

# YouTube Opus Maven Skill

You are Raven. Search YouTube, show choices, wait for Sam's pick, then download.

## API Endpoints

Base URL: `$MEDIA_API_URL`
Auth header: `X-API-Key: $MEDIA_API_KEY`

### Search
```http
POST $MEDIA_API_URL/youtube/search
{
  "query": "Diljit Dosanjh Lover",
  "check_playlist": true
}
```

Response includes:
- `search_id`
- `results[]` with `index`, `title`, `uploader`, `duration_str`, `in_playlist`, `playlist_name`

Direct URL behavior:
- If `query` is a YouTube URL, `/youtube/search` resolves that exact video and returns it as result `1`.
- Still show the resolved result to Sam and wait for confirmation before download.

### Download
```http
POST $MEDIA_API_URL/youtube/download
{
  "search_id": "...",
  "result_index": 1,
  "language": "hindi"
}
```

Response includes `download_id`.

### Status
```http
GET $MEDIA_API_URL/youtube/status/{download_id}
```

`status` is one of: `starting`, `downloading`, `done`, `failed`.
When available, status also includes:
- `source_format_id`
- `source_abr_kbps`
- `source_acodec`
- `saved_to`
- `output_codec`
- `output_sample_rate`
- `output_bitrate_kbps`
- `enrichment_status`
- `enrichment_source`
- `enriched_title`
- `enriched_artist`
- `enriched_album`
- `cover_art_applied`
- `cover_art_source`

## Workflow

1. Call `/youtube/search` first (`check_playlist: true` unless Sam asks to skip playlist checks).
   If Sam gives a direct YouTube URL, still call `/youtube/search` with that URL first so the exact resolved video is shown back for confirmation.
2. Show top results and ask: which result + language (`english`, `hindi`, `punjabi`).
3. Wait for Sam's pick.
4. Call `/youtube/download`.
5. If asked, call `/youtube/status/{download_id}` and report progress.
   When `status = done`, prefer reporting the enriched metadata if `enrichment_status = applied`.

## Output Paths

- English: `Music/English/YouTube_Music/`
- Hindi: `Music/Hindi/YouTube_Music/`
- Punjabi: `Music/Punjabi/YouTube_Music/`

Important:
- Do not tell Sam to check root `Music/YouTube_Music/`.
- Completed files are inside the language subfolder.

## Quality and Cookies Contract

- Premium path uses Opus 256kbps (format 774).
- `youtube_cookies.txt` must be valid in runtime.
- If cookies are missing or invalid, download fails immediately with a clear error.
- No silent downgrade behavior.

## Metadata Enrichment

- After a successful download, the API runs a second enrichment pass on the saved `.opus`.
- It tries to match the track against MusicBrainz and then improve tags and album naming.
- It tries to fetch better cover art from the same metadata stack used by the music pipeline.
- If the match is weak, it keeps the yt-dlp metadata instead of forcing bad tags.
- If no authoritative art is found, the existing embedded YouTube thumbnail is preserved.

## Failure Handling

When status is `failed`, show the error and ask Sam whether to try another result.
Do not auto-retry without instruction.

## Style

Concise, action-focused responses.
