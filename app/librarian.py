"""
Librarian router — handles ebook search, download, and Kavita library management.
Searches Gutendex (Gutenberg), Standard Ebooks, and Archive.org in parallel.
Downloads EPUB/PDF files and routes them to the correct Kavita library folder.
Triggers Kavita library scan after download.
"""
import asyncio
import logging
import os
import re as _re
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from app.config import settings
from app.kavita import KavitaClient
from app.sources.gutendex import search_gutendex
from app.sources.standard_ebooks import search_standard_ebooks
from app.sources.archive_org import search_archive_org

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/librarian", tags=["librarian"])

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(key: Optional[str] = Depends(api_key_header)) -> str:
    if not key or key != settings.API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API key",
        )
    return key


# ---------------------------------------------------------------------------
# Kavita client (shared, module-level)
# ---------------------------------------------------------------------------
kavita = KavitaClient(
    url=settings.KAVITA_URL,
    username=settings.KAVITA_USERNAME,
    password=settings.KAVITA_PASSWORD,
)

# ---------------------------------------------------------------------------
# Path mappings — where to save ebooks on the VPS host
# (Same Google Drive FUSE mount as Jellyfin — write here = saved to Drive + Kavita)
# ---------------------------------------------------------------------------
KAVITA_PATHS: dict[str, str] = {
    "novel":    "/mnt/cloud/gdrive/Media/Books",
    "comic":    "/mnt/cloud/gdrive/Media/Comics",
    "magazine": "/mnt/cloud/gdrive/Media/Magazines",
}

# Kavita library name fragments for scan resolution
KAVITA_LIBRARY_NAMES: dict[str, str] = {
    "novel":    "novels",
    "comic":    "comics",
    "magazine": "magazines",
}

# Valid file extensions for ebooks
_EBOOK_EXTS = {".epub", ".pdf", ".cbz", ".cbr", ".mobi", ".azw3"}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Strip characters that are invalid in Linux filenames."""
    name = _re.sub(r'[<>:"/\\?*\x00-\x1f|]', "", name)
    return _re.sub(r" {2,}", " ", name).strip()


def _build_save_path(category: str, author: str, title: str, fmt: str) -> str:
    """
    Build the full destination path for the ebook.
    Books:    /mnt/cloud/gdrive/Media/Books/{Author Name}/{Title}.epub
    Comics:   /mnt/cloud/gdrive/Media/Comics/{Series Name}/{Title}.epub
    Magazines:/mnt/cloud/gdrive/Media/Magazines/{Publication}/{Title}.epub
    """
    base = KAVITA_PATHS[category]
    safe_author = _safe_filename(author) if author and author != "Unknown" else "Unknown Author"
    safe_title = _safe_filename(title)
    ext = f".{fmt}" if not fmt.startswith(".") else fmt
    return os.path.join(base, safe_author, f"{safe_title}{ext}")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class BookSearchRequest(BaseModel):
    query: str
    limit: int = 5


class BookDownloadRequest(BaseModel):
    download_url: str
    title: str
    author: str = "Unknown"
    category: str  # novel | comic | magazine
    format: str = "epub"  # epub | pdf | cbz | cbr


class LibraryScanRequest(BaseModel):
    category: str  # novel | comic | magazine


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health")
async def librarian_health():
    return {"status": "ok", "service": "librarian"}


@router.post("/search")
async def search_books(req: BookSearchRequest, _: str = Depends(require_api_key)):
    """
    Search Gutendex (Gutenberg), Standard Ebooks, and Archive.org in parallel.
    Results are ranked: Standard Ebooks first, Gutenberg second, Archive.org third.
    EPUB is always preferred over PDF.
    """
    gut_task = asyncio.create_task(search_gutendex(req.query, req.limit))
    se_task = asyncio.create_task(search_standard_ebooks(req.query, req.limit))
    ao_task = asyncio.create_task(search_archive_org(req.query, req.limit))

    # Also check if it's already in Kavita
    kavita_task = asyncio.create_task(kavita.is_in_library(req.query))

    _gut, _se, _ao, _already_in_library = await asyncio.gather(
        gut_task, se_task, ao_task, kavita_task,
        return_exceptions=True,
    )

    gut_results: list = _gut if isinstance(_gut, list) else []
    se_results: list = _se if isinstance(_se, list) else []
    ao_results: list = _ao if isinstance(_ao, list) else []
    already_in_library: bool = _already_in_library if isinstance(_already_in_library, bool) else False

    # Priority: Standard Ebooks → Gutenberg → Archive.org
    combined = se_results + gut_results + ao_results

    # Deduplicate by title (case-insensitive)
    seen_titles: set[str] = set()
    deduped = []
    for result in combined:
        key = result.get("title", "").lower().strip()
        if key and key not in seen_titles:
            seen_titles.add(key)
            deduped.append(result)

    # Add index numbers
    for i, item in enumerate(deduped, start=1):
        item["index"] = i

    # Build source breakdown
    sources: dict[str, int] = {}
    for r in deduped:
        src = r.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1

    return {
        "query": req.query,
        "already_in_kavita": already_in_library,
        "results": deduped,
        "total_found": len(deduped),
        "sources": sources,
    }


@router.post("/download")
async def download_book(req: BookDownloadRequest, _: str = Depends(require_api_key)):
    """
    Download an ebook from the given URL, save to the correct folder,
    and trigger a Kavita library scan.

    Category determines where the file is saved:
      - novel    → /mnt/cloud/gdrive/Media/Books/{Author}/{Title}.epub
      - comic    → /mnt/cloud/gdrive/Media/Comics/{Author}/{Title}.epub
      - magazine → /mnt/cloud/gdrive/Media/Magazines/{Author}/{Title}.epub
    """
    if req.category not in KAVITA_PATHS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown category '{req.category}'. Valid: {list(KAVITA_PATHS.keys())}",
        )

    # Build destination path
    save_path = _build_save_path(req.category, req.author, req.title, req.format)
    save_dir = os.path.dirname(save_path)

    # Check if already exists
    if os.path.exists(save_path):
        return {
            "success": True,
            "message": "File already exists — skipping download",
            "saved_to": save_path,
            "already_existed": True,
            "scan_triggered": False,
        }

    # Download the file
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(req.download_url)
            resp.raise_for_status()
            content = resp.content
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to download ebook: [{type(exc).__name__}] {exc}",
        )

    # Validate it looks like an ebook (basic check)
    if len(content) < 1000:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Downloaded file is too small — likely an error page, not an ebook",
        )

    # Save to disk
    try:
        os.makedirs(save_dir, exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(content)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save file: {exc}",
        )

    size_mb = round(len(content) / (1024 * 1024), 2)

    # Trigger Kavita library scan
    scan_triggered = False
    scan_error = None
    try:
        library_name = KAVITA_LIBRARY_NAMES[req.category]
        library_id = await kavita.get_library_id(library_name)
        if library_id:
            scan_triggered = await kavita.scan_library(library_id)
        else:
            scan_error = f"Could not find Kavita library matching '{library_name}'"
    except Exception as exc:
        scan_error = str(exc)
        logger.warning("Kavita scan failed after download: %s", exc)

    return {
        "success": True,
        "message": f"'{req.title}' downloaded successfully",
        "saved_to": save_path,
        "size_mb": size_mb,
        "format": req.format,
        "already_existed": False,
        "scan_triggered": scan_triggered,
        "scan_error": scan_error,
    }


@router.post("/scan")
async def scan_library(req: LibraryScanRequest, _: str = Depends(require_api_key)):
    """Manually trigger a Kavita library scan for a given category."""
    if req.category not in KAVITA_LIBRARY_NAMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown category '{req.category}'. Valid: {list(KAVITA_LIBRARY_NAMES.keys())}",
        )

    library_name = KAVITA_LIBRARY_NAMES[req.category]
    try:
        library_id = await kavita.get_library_id(library_name)
        if not library_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No Kavita library found matching '{library_name}'",
            )
        triggered = await kavita.scan_library(library_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kavita scan failed: {exc}",
        )

    return {
        "status": "scan triggered" if triggered else "scan failed",
        "library_name": library_name,
        "library_id": library_id,
    }


@router.get("/status")
async def library_status(title: Optional[str] = None, _: str = Depends(require_api_key)):
    """Check if a title is already in Kavita library."""
    if not title:
        # Return list of all libraries
        try:
            libraries = await kavita.get_libraries()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Cannot reach Kavita: {exc}",
            )
        return {"libraries": libraries}

    try:
        in_library = await kavita.is_in_library(title)
        search_result = await kavita.search(title)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kavita search failed: {exc}",
        )

    return {
        "title": title,
        "in_kavita": in_library,
        "matches": search_result.get("series", [])[:3],
    }
