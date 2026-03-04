# Librarian / Kavita / Anna's Archive — Current State

**Last updated: 2026-03-04**
**Status: Fully implemented and validated end-to-end**

---

## What Is Working (all confirmed on live VPS)

### Search pipeline
- `POST /librarian/search` queries four sources in parallel:
  1. Standard Ebooks (SE) — public domain, high quality EPUB
  2. Gutendex / Project Gutenberg — 70k+ public domain titles
  3. Archive.org — large collection, variable quality
  4. Anna's Archive — modern/commercial titles via HTML scraping
- Results deduplicated by title (case-insensitive), ranked SE → Gutenberg → Archive.org → AA
- `already_in_kavita` flag works correctly — queries Kavita by title, returns true/false
- AA results include `source_id="/md5/..."` and a clickable `download_url` pointing to the AA detail page

### Download pipeline
- Standard sources (SE/Gutenberg/Archive.org): pass `download_url` directly
- Anna's Archive: pass `source="AnnasArchive"` + `source_id="/md5/..."` — resolver fires automatically
- EPUB structural validation runs before Kavita scan:
  - Opens file as ZIP
  - Checks META-INF/container.xml exists
  - Resolves OPF package path from container.xml
  - Confirms `<dc:title>` is present in OPF
  - If invalid: deletes file, returns HTTP 422 with error detail
- Kavita library scan triggers automatically after valid download
- File already on disk: returns `already_existed: true` without re-downloading

### Anna's Archive resolver
- Primary path: `libgen.li/ads.php?md5={md5}` → parse `[GET]` link → return `get.php?md5=...&key=...` URL
- Libgen key is time-based, NOT session-tied — works across separate httpx client instances
- Fallback path: `slow_download/{md5}/0/0` with optional `ANNA_ARCHIVE_COOKIE` — only attempted if Libgen fails
- DDoS-Guard check: if slow_download returns a DDoS-Guard challenge, raises RuntimeError → HTTP 503
- Confirmed working from VPS without any cookie (Libgen path sufficient)

### Kavita integration
- `KAVITA_URL=http://172.17.0.1:8091` — Docker bridge gateway IP (NOT localhost)
  - `localhost` inside a Docker container refers to the container itself, not the VPS host
  - `172.17.0.1` is the default Docker bridge gateway and correctly reaches Kavita on the host
- Kavita library: "Novels n Books" (matched via partial name fragment "novels")
- Books path: `/mnt/cloud/gdrive/Media/Books/{Author}/{Title}.epub`
- Container mount: host `/mnt/cloud/gdrive/Media/Books` → Kavita container `/data/Books`
- Scan is asynchronous — typically completes within 5–15 seconds after trigger

---

## Known Edge Case: Title Mismatch (Asterisk in EPUB Metadata)

Some EPUBs have stylized titles in their internal `dc:title` metadata that differ from the
scraped search result title.

Example:
- Scraped title (used as filename): `Everything Is Fucked.epub`
- EPUB dc:title (what Kavita indexes): `Everything Is F*cked (9780062888471)`
- Effect: `already_in_kavita` search for "Everything Is Fucked" returns `false`

This does NOT cause data corruption:
- The download endpoint guards against re-download via `os.path.exists(save_path)`
- The save path is built from the title in the request — which matches the filename on disk
- A re-download attempt with the same title returns `already_existed: true`

The mismatch is cosmetic: the book is in the library and accessible; the duplicate-detection
check for that specific title will return false until queried with the exact Kavita-indexed name.

---

## Source Notes

### Standard Ebooks
- Server-side download requests are **blocked by SE** — they return an "Your Download Has Started"
  XHTML page (~8KB) instead of the actual EPUB
- EPUB validation catches this (XHTML page fails ZIP check → HTTP 422)
- SE results are still shown in search (useful for identifying availability)
- If an SE result is selected and fails with 422, pick an alternative source

### Gutenberg / Gutendex
- Works reliably for public domain titles
- EPUB quality is generally good

### Archive.org
- Highly variable quality; some EPUBs fail Kavita parsing
- EPUB validation catches most bad files before Kavita ingestion

### Anna's Archive
- Best source for modern/commercial titles
- Libgen resolver works reliably from VPS without authentication
- Mirror fallback order: annas-archive.gl → annas-archive.li → annas-archive.se
- Search filters by `ext=epub` automatically

---

## Confirmed End-to-End Tests (2026-03-04)

| Test | Result |
|---|---|
| Search "Everything Is Fucked" — AA results appear | PASS |
| Download via Libgen resolver | PASS |
| EPUB validation passes | PASS |
| Kavita scan triggered | PASS |
| Book appears in Kavita | PASS |
| Search "The Subtle Art of Not Giving a Fuck" | PASS |
| Download result (AnnasArchive, 0.6MB EPUB) | PASS |
| `scan_triggered: true`, `scan_error: null` | PASS |
| `in_kavita: true` after 15s | PASS |
| `already_in_kavita: true` for Pride and Prejudice (pre-existing) | PASS |
| `already_in_kavita: false` for new titles | PASS |

---

## Configuration Reference

`.env` keys relevant to Librarian:

```bash
KAVITA_URL=http://172.17.0.1:8091   # Docker bridge host IP — do NOT use localhost
KAVITA_USERNAME=<username>
KAVITA_PASSWORD=<password>
ANNA_ARCHIVE_COOKIE=                 # Optional — for slow_download fallback only
```

---

## Remaining Gaps / Future Work

1. **Title mismatch duplicate detection** — if EPUB metadata title differs from scraped title,
   `already_in_kavita` may return false for an existing book. Low priority (disk check prevents
   actual re-download; mismatch only affects the advisory flag).

2. **Standard Ebooks downloads blocked** — SE blocks server-side requests. Validation catches it
   with 422, but the user must pick a different source. Could scrape the SE EPUB file URL directly
   if needed in future.

3. **Comic/magazine sources** — no Anna's Archive equivalent for comics yet. Archive.org is the
   primary fallback for CBZ/CBR files.
