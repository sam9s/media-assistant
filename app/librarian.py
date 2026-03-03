"""
Librarian router — handles ebook search, download, and Kavita library management.
Searches Standard Ebooks, Gutendex, Archive.org, and Anna's Archive in parallel.
Downloads EPUB/PDF files and routes them to the correct Kavita library folder.
Validates EPUBs before triggering Kavita scan to avoid Kavita parse failures.
Triggers Kavita library scan after a successful, validated download.
"""
import asyncio
import io
import logging
import os
import re as _re
import xml.etree.ElementTree as _ET
import zipfile
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from app.config import settings
from app.kavita import KavitaClient
from app.sources.archive_org import search_archive_org
from app.sources.annas_archive import resolve_annas_download, search_annas_archive
from app.sources.gutendex import search_gutendex
from app.sources.standard_ebooks import search_standard_ebooks

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


def _validate_epub(content: bytes) -> tuple[bool, str]:
    """
    Lightweight structural EPUB validation.
    Returns (is_valid, error_message).

    Checks:
      1. File is a valid ZIP archive
      2. Contains META-INF/container.xml
      3. container.xml points to a valid OPF package file
      4. OPF package contains a non-empty <dc:title> element

    This catches the files that Kavita rejects at parse time with
    "Unable to parse any meaningful information out of file".
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return False, "File is not a valid ZIP/EPUB archive"

    names = zf.namelist()

    if "META-INF/container.xml" not in names:
        return False, "EPUB is missing META-INF/container.xml"

    try:
        container_xml = zf.read("META-INF/container.xml")
        container = _ET.fromstring(container_xml)
    except Exception as exc:
        return False, f"Could not parse META-INF/container.xml: {exc}"

    ns = {"ns": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfiles = container.findall(".//ns:rootfile", ns)
    if not rootfiles:
        return False, "container.xml has no <rootfile> element"

    opf_path = rootfiles[0].get("full-path", "")
    if not opf_path or opf_path not in names:
        return False, f"OPF package file '{opf_path}' not found in EPUB"

    try:
        opf_xml = zf.read(opf_path)
        opf = _ET.fromstring(opf_xml)
    except Exception as exc:
        return False, f"Could not parse OPF package '{opf_path}': {exc}"

    dc_ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    title_el = opf.find(".//dc:title", dc_ns)
    if title_el is None or not (title_el.text or "").strip():
        return False, "EPUB OPF package has no <dc:title> metadata"

    return True, ""


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class BookSearchRequest(BaseModel):
    query: str
    limit: int = 5


class BookDownloadRequest(BaseModel):
    # Standard sources (SE, Gutenberg, Archive.org): provide download_url
    download_url: Optional[str] = None
    # Anna's Archive: provide source="AnnasArchive" and source_id="/md5/..."
    source: Optional[str] = None
    source_id: Optional[str] = None
    # Common fields
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
    Search Standard Ebooks, Gutenberg, Archive.org, and Anna's Archive in parallel.
    Results are ranked: Standard Ebooks → Gutenberg → Archive.org → Anna's Archive.
    EPUB is always preferred over PDF. Deduplicates by title.
    Anna's Archive results include source_id instead of download_url — use
    source="AnnasArchive" + source_id when calling /download for those results.
    """
    gut_task = asyncio.create_task(search_gutendex(req.query, req.limit))
    se_task = asyncio.create_task(search_standard_ebooks(req.query, req.limit))
    ao_task = asyncio.create_task(search_archive_org(req.query, req.limit))
    aa_task = asyncio.create_task(search_annas_archive(req.query, req.limit))

    # Also check if it's already in Kavita
    kavita_task = asyncio.create_task(kavita.is_in_library(req.query))

    _gut, _se, _ao, _aa, _already_in_library = await asyncio.gather(
        gut_task, se_task, ao_task, aa_task, kavita_task,
        return_exceptions=True,
    )

    gut_results: list = _gut if isinstance(_gut, list) else []
    se_results: list = _se if isinstance(_se, list) else []
    ao_results: list = _ao if isinstance(_ao, list) else []
    aa_results: list = _aa if isinstance(_aa, list) else []
    already_in_library: bool = _already_in_library if isinstance(_already_in_library, bool) else False

    # Priority: Standard Ebooks → Gutenberg → Archive.org → Anna's Archive
    combined = se_results + gut_results + ao_results + aa_results

    # Deduplicate by title (case-insensitive) — first occurrence wins (highest priority)
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
    Download an ebook and save it to the correct Kavita folder.
    For standard sources (SE/Gutenberg/Archive.org): pass download_url.
    For Anna's Archive results: pass source="AnnasArchive" and source_id="/md5/...".

    EPUBs are validated for structural integrity before triggering a Kavita scan.
    If validation fails the file is removed from disk and a 422 is returned.

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

    # Resolve download URL
    if req.source == "AnnasArchive":
        if not req.source_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source_id (e.g. '/md5/abc123') is required when source='AnnasArchive'",
            )
        try:
            aa_cookie = settings.ANNA_ARCHIVE_COOKIE or None
            download_url = await resolve_annas_download(req.source_id, cookie=aa_cookie)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            )
    elif req.download_url:
        download_url = req.download_url
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either download_url or source='AnnasArchive' with source_id",
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
            "kavita_safe": True,
            "scan_triggered": False,
        }

    # Download the file
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(download_url)
            resp.raise_for_status()
            content = resp.content
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to download ebook: [{type(exc).__name__}] {exc}",
        )

    # Validate minimum size
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

    # EPUB structural validation — run after saving, delete file if invalid
    kavita_safe = True
    epub_error = None
    if req.format.lower() == "epub":
        valid, err = _validate_epub(content)
        if not valid:
            kavita_safe = False
            epub_error = err
            try:
                os.remove(save_path)
                # Remove the author folder if it's now empty
                if not os.listdir(save_dir):
                    os.rmdir(save_dir)
            except Exception:
                pass
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": f"EPUB validation failed — file rejected before Kavita scan",
                    "epub_error": epub_error,
                    "kavita_safe": False,
                    "hint": "Try a different result (better source) for this title",
                },
            )

    # Trigger Kavita library scan (only for valid files)
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
        "kavita_safe": kavita_safe,
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
