from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field

from app.config import settings
from app.recommendation_engine import (
    generate_recommendations,
    ingest_status,
    latest_recommendation_digest,
    query_recommendations,
    recommendation_history,
    run_ingestion,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def _require_api_key(key: Optional[str] = Security(_api_key_header)) -> str:
    if key != settings.API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return key or ""


class IngestRunRequest(BaseModel):
    sources: list[Literal["jellyfin", "navidrome", "kavita"]] = Field(
        default_factory=lambda: ["jellyfin", "navidrome", "kavita"]
    )
    window_days: int = Field(default_factory=lambda: settings.RECOMMENDATIONS_INGEST_WINDOW_DAYS, ge=1, le=365)
    rebuild_signals: bool = True


class GenerateRecommendationsRequest(BaseModel):
    mode: Literal["weekly", "ondemand"] = "ondemand"
    lookback_days: int = Field(default_factory=lambda: settings.RECOMMENDATIONS_DEFAULT_LOOKBACK_DAYS, ge=1, le=90)
    library_only: bool = False
    include_new_finds: bool = True
    limit_per_type: int = Field(default=1, ge=1, le=5)


class RecommendationQueryRequest(BaseModel):
    seed_type: Literal["movie", "music", "book", "theme"] = "theme"
    seed_value: str = Field(..., min_length=1)
    include_library: bool = True
    include_external: bool = True


@router.get("/health")
async def recommendation_health(_: str = Depends(_require_api_key)):
    return {
        "status": "ok",
        "enabled": settings.RECOMMENDATIONS_ENABLED,
    }


@router.post("/ingest/run")
async def recommendation_ingest_run(body: IngestRunRequest, _: str = Depends(_require_api_key)):
    if not settings.RECOMMENDATIONS_ENABLED:
        raise HTTPException(status_code=503, detail="Recommendations are disabled")
    try:
        result = run_ingestion(
            sources=body.sources,
            window_days=body.window_days,
            rebuild_signals_flag=body.rebuild_signals,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Recommendation ingest failed: {type(exc).__name__}: {exc}")
    return result


@router.get("/ingest/status")
async def recommendation_ingest_status(_: str = Depends(_require_api_key)):
    if not settings.RECOMMENDATIONS_ENABLED:
        raise HTTPException(status_code=503, detail="Recommendations are disabled")
    try:
        return ingest_status()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Recommendation ingest status failed: {type(exc).__name__}: {exc}")


@router.post("/generate")
async def recommendation_generate(body: GenerateRecommendationsRequest, _: str = Depends(_require_api_key)):
    if not settings.RECOMMENDATIONS_ENABLED:
        raise HTTPException(status_code=503, detail="Recommendations are disabled")
    try:
        return await generate_recommendations(
            mode=body.mode,
            lookback_days=body.lookback_days,
            library_only=body.library_only,
            include_new_finds=body.include_new_finds,
            limit_per_type=body.limit_per_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Recommendation generation failed: {type(exc).__name__}: {exc}")


@router.get("/latest")
async def recommendation_latest(_: str = Depends(_require_api_key)):
    if not settings.RECOMMENDATIONS_ENABLED:
        raise HTTPException(status_code=503, detail="Recommendations are disabled")
    try:
        digest = latest_recommendation_digest()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Recommendation fetch failed: {type(exc).__name__}: {exc}")
    return {"latest": digest}


@router.post("/query")
async def recommendation_query(body: RecommendationQueryRequest, _: str = Depends(_require_api_key)):
    if not settings.RECOMMENDATIONS_ENABLED:
        raise HTTPException(status_code=503, detail="Recommendations are disabled")
    try:
        return await query_recommendations(
            seed_type=body.seed_type,
            seed_value=body.seed_value,
            include_library=body.include_library,
            include_external=body.include_external,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Recommendation query failed: {type(exc).__name__}: {exc}")


@router.get("/history")
async def recommendation_digest_history(limit: int = 20, _: str = Depends(_require_api_key)):
    if not settings.RECOMMENDATIONS_ENABLED:
        raise HTTPException(status_code=503, detail="Recommendations are disabled")
    try:
        return {"items": recommendation_history(limit=max(1, min(limit, 100)))}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Recommendation history failed: {type(exc).__name__}: {exc}")

