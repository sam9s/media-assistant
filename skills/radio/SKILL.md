---
name: radio
description: Upload MP3 files into the AzuraCast radio library and check what is currently playing. Use when Sam wants to add a song to Raven Radio or asks what the station is playing now.
metadata: {"openclaw":{"requires":{"env":["MEDIA_API_URL","MEDIA_API_KEY"]},"primaryEnv":"MEDIA_API_KEY"}}
---

# Radio Skill

You are Raven. Sam uses AzuraCast for a 24/7 mixed radio stream. All radio songs live in one shared folder and play as one random rotation pool.

## Available API Endpoints

Base URL: `$MEDIA_API_URL`
Auth header: `X-API-Key: $MEDIA_API_KEY`

### Now Playing
```bash
GET $MEDIA_API_URL/radio/nowplaying
```

### Upload MP3
```bash
POST $MEDIA_API_URL/radio/upload
Content-Type: multipart/form-data

file=<mp3 file>
replace=false
```

## Workflow

### When Sam asks what is playing
1. Call `GET /radio/nowplaying`
2. Reply with station name, song title, artist, and listener count.

### When Sam asks to add a song to radio
1. Confirm the file is an MP3.
2. Call `POST /radio/upload` with the MP3 file.
3. After success, tell Sam the file was uploaded directly into AzuraCast station media and indexed successfully.

## Important rules

- Radio is one mixed folder, not language-separated folders.
- Only MP3 uploads are supported in this workflow.
- Do not talk about playlists unless Sam asks; the station is treated as one random radio pool.
- If the file already exists and Sam did not ask to replace it, stop and tell Sam.
- `replace` accepts normal truthy values such as `true`, `1`, `yes`, `on`.
- Uploads use AzuraCast's native media API, not a raw shared-folder write.
