"""Smoke test for health endpoints."""

from app.api.v1 import router as api_router


async def test_healthz(client) -> None:
    res = await client.get("/api/v1/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


async def test_readyz_healthy(client, monkeypatch) -> None:
    async def _ok() -> str:
        return "ok"

    monkeypatch.setattr(api_router, "check_redis", _ok)

    res = await client.get("/api/v1/readyz")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["checks"] == {"database": "ok", "redis": "ok"}


async def test_readyz_redis_down(client, monkeypatch) -> None:
    async def _fail() -> str:
        return "ConnectionError: redis unreachable"

    monkeypatch.setattr(api_router, "check_redis", _fail)

    res = await client.get("/api/v1/readyz")
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "error"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"].startswith("ConnectionError")
