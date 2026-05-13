"""API v1 router."""

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine
from app.modules.auth.router import router as auth_router
from app.modules.characters.router import router as characters_router
from app.modules.chats.router import router as chats_router
from app.modules.dungeon_master.router import router as dm_router
from app.modules.images.router import router as images_router
from app.modules.users.router import router as users_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(characters_router)
router.include_router(chats_router)
router.include_router(dm_router)
router.include_router(images_router)


class HealthResponse(BaseModel):
    """Health check response model."""

    status: str


class ReadinessResponse(BaseModel):
    """Readiness check response model with per-dependency status."""

    status: str
    checks: dict[str, str]


@router.get("/healthz", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Basic health check endpoint."""
    return HealthResponse(status="ok")


async def check_db() -> str:
    """Return ``"ok"`` if the database accepts a trivial query, else error detail."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


async def check_redis() -> str:
    """Return ``"ok"`` if Redis responds to PING, else error detail."""
    try:
        from redis.asyncio import from_url

        client = from_url(settings.REDIS_URL, socket_connect_timeout=1.0, socket_timeout=1.0)
        try:
            await client.ping()
            return "ok"
        finally:
            await client.aclose()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


@router.get("/readyz", response_model=ReadinessResponse)
async def readiness_check(response: Response) -> ReadinessResponse:
    """Readiness check: verifies the app can reach Postgres and Redis."""
    checks = {
        "database": await check_db(),
        "redis": await check_redis(),
    }
    healthy = all(v == "ok" for v in checks.values())
    if not healthy:
        response.status_code = 503
    return ReadinessResponse(status="ok" if healthy else "error", checks=checks)
