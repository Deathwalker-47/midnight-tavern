"""Shared pytest fixtures.

Tests run against an in-memory SQLite database so they're hermetic. JSONB and
ARRAY columns degrade to TEXT/JSON under SQLite, but every model is built on
SQLAlchemy `JSONB` from `sqlalchemy.dialects.postgresql`, which SQLAlchemy
maps to a JSON-compatible type when the dialect is sqlite.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

# Set env BEFORE importing app modules — Settings() reads at import time.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-please-do-not-use-in-prod")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("IMAGE_PROVIDER_ORDER", "dummy")
os.environ.setdefault("IMAGES_STORAGE_DIR", "./var/test-images")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB, UUID  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles  # noqa: E402


# Map Postgres-specific types to SQLite-compatible equivalents so the same
# model definitions can be compiled for both dialects.
@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@compiles(UUID, "sqlite")
def _compile_uuid_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "CHAR(36)"

# Import db.__init__ which registers every model on Base.metadata
from app.db import Base  # noqa: E402,F401
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture
async def engine(tmp_path_factory) -> AsyncGenerator:
    """Per-test file-backed SQLite engine + schema.

    File-backed (not in-memory) so background tasks and request handlers can
    open independent connections to the same DB. NullPool keeps the test
    teardown clean — no lingering connections to dispose.
    """
    from sqlalchemy.pool import NullPool

    db_path = tmp_path_factory.mktemp("db") / "test.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_maker(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(session_maker, monkeypatch) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX async client wired to override get_db.

    Also redirects ``app.db.session.AsyncSessionLocal`` (used by background
    tasks such as image-job runners) to the test session factory so they hit
    the same in-memory DB."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    # Redirect the module-level session factory used by background tasks.
    import app.db.session as session_module
    import app.modules.images.service as image_service

    monkeypatch.setattr(session_module, "AsyncSessionLocal", session_maker)
    monkeypatch.setattr(image_service, "AsyncSessionLocal", session_maker)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def unique_username() -> str:
    return f"user_{uuid.uuid4().hex[:8]}"
