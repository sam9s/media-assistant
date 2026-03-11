from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader

from app.config import settings
from app.music import get_music_job, list_music_jobs
from app.youtube import get_youtube_job, list_youtube_jobs

router = APIRouter(prefix="/jobs", tags=["jobs"])

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: str = Security(_api_key_header)) -> str:
    if key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key


@router.get("")
async def list_jobs(_: str = Depends(_require_api_key), pipeline: str | None = None):
    jobs = list_music_jobs() + list_youtube_jobs()
    jobs.sort(key=lambda row: row.get("updated_at") or 0, reverse=True)
    if pipeline:
        jobs = [job for job in jobs if job["pipeline"] == pipeline]
    return {"count": len(jobs), "jobs": jobs}


@router.get("/{job_id}")
async def get_job(job_id: str, _: str = Depends(_require_api_key)):
    job = get_music_job(job_id) or get_youtube_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id not found")
    return job
