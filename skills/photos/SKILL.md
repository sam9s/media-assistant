---
name: photos
description: Basic Immich photo management for upload, album creation, album rename/delete, asset search, and simple asset download/update. Use when Sam asks to upload a photo, create or manage a photo album, search for an image in Immich, or download a specific asset.
metadata: {"openclaw":{"requires":{"env":["MEDIA_API_URL","MEDIA_API_KEY"]},"primaryEnv":"MEDIA_API_KEY"}}
---

# Photos Skill

You are Raven, managing Sam's Immich photo library at `photos.sam9scloud.in`.

Keep this skill intentionally simple. This is basic photo management only.

## Use Cases

Use this skill when Sam asks to:
- upload a photo or video into Immich
- create an album
- add uploaded assets to an album
- rename an album
- delete an album
- search for a photo by filename
- update simple asset fields
- download a specific asset from Immich

Do not invent advanced photo automation:
- no face clustering logic
- no map workflows
- no AI curation
- no bulk reorganizing unless Sam asks explicitly

## API Endpoints

Base URL: `$MEDIA_API_URL`
Auth header: `X-API-Key: $MEDIA_API_KEY`

### Upload a photo
`POST $MEDIA_API_URL/photos/upload`

Multipart form fields:
- `file` — required
- `album_id` — optional
- `album_name` — optional; create or reuse album by name
- `is_favorite` — optional boolean

### List albums
`GET $MEDIA_API_URL/photos/albums`

Optional query:
- `name`

### Create album
`POST $MEDIA_API_URL/photos/albums`

```json
{
  "album_name": "Trip to Goa",
  "description": "March trip",
  "asset_ids": []
}
```

### Rename/update album
`PATCH $MEDIA_API_URL/photos/albums/{album_id}`

```json
{
  "album_name": "Goa Trip 2026",
  "description": "Updated description"
}
```

### Delete album
`DELETE $MEDIA_API_URL/photos/albums/{album_id}`

### Add assets to album
`POST $MEDIA_API_URL/photos/albums/{album_id}/assets`

```json
{
  "asset_ids": ["asset-id-1", "asset-id-2"]
}
```

### Search assets
`POST $MEDIA_API_URL/photos/search`

```json
{
  "original_file_name": "IMG_1234",
  "album_ids": [],
  "page": 1,
  "size": 50,
  "with_deleted": false
}
```

### Update an asset
`PATCH $MEDIA_API_URL/photos/assets/{asset_id}`

```json
{
  "original_file_name": "Beach Sunset.jpg",
  "description": "Sunset from Candolim",
  "is_favorite": true
}
```

### Download a specific asset
`GET $MEDIA_API_URL/photos/assets/{asset_id}/download`

## Workflow Rules

1. Upload flow:
- upload first
- if `album_name` is provided, find or create the album
- attach the uploaded asset to that album
- report returned asset id and album id

2. Album flow:
- list/search first if the target album is ambiguous
- do not delete an album unless Sam is explicit

3. Download flow:
- identify the asset first
- then download that exact asset id

4. Reporting:
- say what was uploaded
- which album was used
- returned asset id / album id

## Operational Note

Immich is treated as a light-use service in Sam's stack. Prefer stable, basic operations only.
