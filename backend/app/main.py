"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.core.middleware import RequestIDMiddleware
from app.modules.images.lora import init_lora_manager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup/shutdown."""
    setup_logging()

    # Best-effort DB ping (skip in tests where engine is in-memory and may not
    # be the one the test fixture wires up).
    try:
        from app.db.session import engine
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        pass

    # Load LoRA dictionary if present (no-op if file missing).
    lora_dict_path = Path(__file__).resolve().parents[1] / "data" / "master_lora_dict.json"
    init_lora_manager(lora_dict_path)

    yield

    from app.db.session import engine
    await engine.dispose()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="Midnight Tavern API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID middleware
    app.add_middleware(RequestIDMiddleware)

    # Error handlers
    register_error_handlers(app)

    # API routers
    app.include_router(v1_router, prefix="/api/v1")

    # Static mount for generated images. Directory is created on demand by
    # LocalStorage on first write, but mount lazily/safely here.
    images_dir = Path(settings.IMAGES_STORAGE_DIR)
    images_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/images", StaticFiles(directory=str(images_dir)), name="static-images")

    return app


app = create_app()
