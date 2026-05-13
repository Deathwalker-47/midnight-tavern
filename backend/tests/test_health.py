"""Smoke test for health endpoints."""


async def test_healthz(client) -> None:
    res = await client.get("/api/v1/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


async def test_readyz(client) -> None:
    res = await client.get("/api/v1/readyz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
