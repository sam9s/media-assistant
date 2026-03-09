---
name: radio-audio-import
description: Recursively scan a folder for radio candidates, skip FLAC and non-audio targets, extract MP3 audio from MP4/WEBM when needed, and upload the resulting MP3 files into the AzuraCast radio library. Use when Sam wants to bulk-import mixed local media into Raven Radio.
metadata: {"openclaw":{"requires":{"env":["MEDIA_API_URL","MEDIA_API_KEY"]},"primaryEnv":"MEDIA_API_KEY"}}
---

# Radio Audio Import Skill

Use this skill when Sam points to a local folder that contains mixed media and wants radio-safe audio imported into AzuraCast.

Primary tool:
- `scripts/import_to_radio.py`

## Scope

- recurse through a user-provided folder
- include:
  - `.mp3`
  - `.mp4`
  - `.webm`
- exclude:
  - `.flac`
  - images
  - unrelated document/archive files
- for `.mp4` / `.webm`:
  - extract audio to temporary `.mp3` with `ffmpeg`
- upload final `.mp3` files through:
  - `POST $MEDIA_API_URL/radio/upload`

## Rules

- Never send FLAC files to the radio pipeline.
- Do not treat video presence as a blocker if the file is really being used as an audio source.
- Default target format for radio extraction is `MP3`, not `Opus`.
- Preserve the source files; create temporary converted files only when needed.
- If Sam wants a dry run first, report the candidate list before uploading anything.

## Workflow

### 1. Inventory the folder

Run:

```bash
python skills/radio-audio-import/scripts/import_to_radio.py <source_dir> --api-url <MEDIA_API_URL> --api-key <MEDIA_API_KEY> --dry-run
```

This reports:
- direct upload candidates: `.mp3`
- convert then upload candidates: `.mp4`, `.webm`
- skipped files, including `.flac`

### 2. Convert non-MP3 sources

Use `ffmpeg` for `.mp4` and `.webm`:

```bash
ffmpeg -y -i <input> -vn -c:a libmp3lame -q:a 2 <temp-output>.mp3
```

Notes:
- `-vn` removes video.
- `libmp3lame -q:a 2` is the default radio extraction setting unless Sam asks otherwise.
- Temporary output should be placed outside the repo when practical, such as `%TEMP%` or another disposable working path.
- The script auto-discovers `ffmpeg` from:
  - `--ffmpeg-path`
  - `FFMPEG_PATH`
  - `C:\Program Files\ShareX\ffmpeg.exe`
  - known local fallback paths

### 3. Upload to radio

For each final MP3:

```bash
python skills/radio-audio-import/scripts/import_to_radio.py <source_dir> --api-url <MEDIA_API_URL> --api-key <MEDIA_API_KEY> --replace
```

The script uploads through `POST $MEDIA_API_URL/radio/upload`.

### 4. Report clearly

After the run, report:
- source folder scanned
- how many files were skipped
- how many `.mp3` files were uploaded directly
- how many `.mp4` / `.webm` files were converted
- any failures with filenames

## Response style

When reporting a batch, keep it operational:
- uploaded successfully
- converted and uploaded
- skipped because FLAC
- skipped because unsupported type
- failed with reason

## Important boundaries

- This skill is for radio ingestion, not for organizing the main FLAC music library.
- FLAC organization belongs to the music pipeline, not this one.
- Radio uploads ultimately go into AzuraCast via the native upload API and land under the configured radio subfolder.
- Always start with `--dry-run` if Sam asks for a new large folder to be processed.
