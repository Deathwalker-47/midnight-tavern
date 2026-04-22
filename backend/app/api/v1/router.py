"""API v1 router."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.modules.auth.router import router as auth_router
from app.modules.characters.router import router as characters_router
from app.modules.chats.router import router as chats_router
from app.modules.dungeon_master.router import router as dm_router
from app.modules.users.router import router as users_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(characters_router)
router.include_router(chats_router)
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
