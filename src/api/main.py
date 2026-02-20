"""
Media Assistant API
FastAPI backend for media management without LLM censorship
"""

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
import os
import asyncpg
import redis.asyncio as redis
import httpx
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager

# Configuration
POSTGRES_URL = os.getenv("POSTGRES_URL", "postgresql://media_assistant:mediaassistant123@localhost:54322/media_assistant")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
API_KEY = os.getenv("MEDIA_API_KEY", "dev-key")
MASTER_KEY = os.getenv("MASTER_API_KEY", "master-dev-key")

# Service URLs
JELLYFIN_URL = os.getenv("JELLYFIN_URL", "https://movies.sam9scloud.in")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
QBITTORRENT_URL = os.getenv("QBITTORRENT_URL", "http://localhost:8088")
PRIVATEHD_PID = os.getenv("PRIVATEHD_PID", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")

# Global connections
pool: asyncpg.Pool = None
redis_client: redis.Redis = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan"""
    global pool, redis_client
    
    # Startup
    pool = await asyncpg.create_pool(POSTGRES_URL, min_size=5, max_size=20)
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    
    yield
    
    # Shutdown
    await pool.close()
    await redis_client.close()


app = FastAPI(
    title="Media Assistant API",
    description="AI Media Manager without LLM censorship",
    version="1.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============== MODELS ==============

class TorrentSearchRequest(BaseModel):
    query: str
    quality: Optional[str] = "1080p"
    category: str = "movie"
    limit: int = 10


class TorrentAddRequest(BaseModel):
    magnet: str
    category: str = "movie"
    save_path: Optional[str] = None


class DownloadStatus(BaseModel):
    id: str
    title: str
    type: str
    status: str
    progress: int
    download_speed: int
    upload_speed: int
    size: Optional[int]
    downloaded: int


class LibrarySearchRequest(BaseModel):
    query: str
    type: Optional[str] = None
    year: Optional[int] = None
    limit: int = 20


class MediaItem(BaseModel):
    id: str
    title: str
    type: str
    year: Optional[int]
    overview: Optional[str]
    poster_url: Optional[str]
    rating: Optional[float]
    file_path: Optional[str]


# ============== AUTH ==============

async def verify_api_key(x_api_key: str):
    """Verify API key"""
    if x_api_key != API_KEY and x_api_key != MASTER_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


# ============== TORRENT ENDPOINTS ==============

@app.post("/torrents/search", response_model=List[Dict[str, Any]])
async def search_torrents(
    request: TorrentSearchRequest,
    api_key: str = Depends(verify_api_key)
):
    """Search torrents on PrivateHD RSS"""
    try:
        rss_url = f"https://privatehd.to/rss/torrents/{request.category}?pid={PRIVATEHD_PID}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(rss_url, timeout=30.0)
            response.raise_for_status()
        
        # Parse RSS
        root = ET.fromstring(response.text)
        items = []
        
        for item in root.findall('.//item'):
            title = item.find('title')
            link = item.find('link')
            description = item.find('description')
            pub_date = item.find('pubDate')
            
            if title is None:
                continue
                
            title_text = title.text or ""
            
            # Filter by query (case insensitive)
            if request.query.lower() not in title_text.lower():
                continue
            
            # Filter by quality
            if request.quality and request.quality.lower() not in title_text.lower():
                continue
            
            # Extract info
            items.append({
                "title": title_text,
                "link": link.text if link is not None else "",
                "description": description.text if description is not None else "",
                "published": pub_date.text if pub_date is not None else "",
                "quality": request.quality,
                "size": "Unknown"  # Would need to parse description
            })
            
            if len(items) >= request.limit:
                break
        
        return items
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.post("/torrents/add")
async def add_torrent(
    request: TorrentAddRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(verify_api_key)
):
    """Add torrent to qBittorrent and track in database"""
    try:
        # Add to qBittorrent via shim
        async with httpx.AsyncClient() as client:
            qb_response = await client.post(
                f"{QBITTORRENT_URL}/api/qbittorrent/add",
                json={
                    "urls": request.magnet,
                    "savepath": request.save_path or f"/downloads/{request.category}",
                    "category": request.category
                },
                timeout=30.0
            )
            qb_response.raise_for_status()
        
        # Insert into database
        download_id = await pool.fetchval(
            """
            INSERT INTO downloads (title, type, magnet, status, source)
            VALUES ($1, $2, $3, 'queued', 'api')
            RETURNING id
            """,
            request.magnet.split("&dn=")[-1].split("&")[0].replace("+", " ")[:500],
            request.category,
            request.magnet
        )
        
        # Start monitoring in background
        background_tasks.add_task(monitor_download, str(download_id))
        
        return {
            "id": str(download_id),
            "status": "queued",
            "message": "Torrent added successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add torrent: {str(e)}")


@app.get("/torrents/status", response_model=List[DownloadStatus])
async def get_download_status(
    status: Optional[str] = None,
    api_key: str = Depends(verify_api_key)
):
    """Get current download status"""
    try:
        query = "SELECT * FROM downloads"
        params = []
        
        if status:
            query += " WHERE status = $1"
            params.append(status)
        
        query += " ORDER BY created_at DESC LIMIT 50"
        
        rows = await pool.fetch(query, *params)
        
        return [
            DownloadStatus(
                id=str(row["id"]),
                title=row["title"],
                type=row["type"],
                status=row["status"],
                progress=row["progress"],
                download_speed=row["download_speed"],
                upload_speed=row["upload_speed"],
                size=row["size"],
                downloaded=row["downloaded"]
            )
            for row in rows
        ]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get status: {str(e)}")


# ============== LIBRARY ENDPOINTS ==============

@app.post("/library/search", response_model=List[MediaItem])
async def search_library(
    request: LibrarySearchRequest,
    api_key: str = Depends(verify_api_key)
):
    """Search media library (Jellyfin integration)"""
    try:
        async with httpx.AsyncClient() as client:
            headers = {"X-Emby-Token": JELLYFIN_API_KEY}
            response = await client.get(
                f"{JELLYFIN_URL}/Items",
                headers=headers,
                params={
                    "searchTerm": request.query,
                    "limit": request.limit,
                    "recursive": "true",
                    "includeItemTypes": request.type or "Movie,Series"
                },
                timeout=30.0
            )
            response.raise_for_status()
        
        data = response.json()
        items = []
        
        for item in data.get("Items", []):
            items.append(MediaItem(
                id=item.get("Id", ""),
                title=item.get("Name", ""),
                type=item.get("Type", ""),
                year=item.get("ProductionYear"),
                overview=item.get("Overview"),
                poster_url=f"{JELLYFIN_URL}/Items/{item.get('Id')}/Images/Primary" if item.get("Id") else None,
                rating=item.get("CommunityRating"),
                file_path=None
            ))
        
        return items
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.get("/library/stats")
async def get_library_stats(api_key: str = Depends(verify_api_key)):
    """Get library statistics"""
    try:
        # Get from database first
        stats = await pool.fetchrow(
            """
            SELECT 
                COUNT(*) FILTER (WHERE type = 'movie') as movies,
                COUNT(*) FILTER (WHERE type = 'show') as shows,
                COUNT(*) FILTER (WHERE type = 'music') as music
            FROM library_items
            """
        )
        
        return {
            "movies": stats["movies"] if stats else 0,
            "shows": stats["shows"] if stats else 0,
            "music": stats["music"] if stats else 0,
            "source": "database"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


# ============== BACKGROUND TASKS ==============

async def monitor_download(download_id: str):
    """Monitor download progress in background"""
    try:
        # This would poll qBittorrent for progress
        # For now, just update status
        await pool.execute(
            "UPDATE downloads SET status = 'downloading' WHERE id = $1",
            download_id
        )
        
        # TODO: Implement actual monitoring loop
        # - Poll qBittorrent API
        # - Update progress
        # - Detect completion
        # - Trigger post-processing
        
    except Exception as e:
        print(f"Monitor error: {e}")


# ============== HEALTH & INFO ==============

@app.get("/health")
async def health_check():
    """API health check"""
    try:
        # Check database
        await pool.fetchval("SELECT 1")
        
        # Check Redis
        await redis_client.ping()
        
        return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Unhealthy: {str(e)}")


@app.get("/")
async def root(api_key: str = Depends(verify_api_key)):
    """API info - requires authentication"""
    return {
        "name": "Media Assistant API",
        "version": "1.0.0",
        "endpoints": [
            "/torrents/search",
            "/torrents/add",
            "/torrents/status",
            "/library/search",
            "/library/stats",
            "/health"
        ]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
