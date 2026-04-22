"""API v1 router with health check endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.modules.dungeon_master.router import router as dm_router

router = APIRouter()
router.include_router(dm_router)


class HealthResponse(BaseModel):
    """Health check response model."""

    status: str


@router.get("/healthz", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Basic health check endpoint."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse)
async def readiness_check() -> HealthResponse:
    """Readiness check endpoint (placeholder for DB/Redis checks)."""
    return HealthResponse(status="ok")
