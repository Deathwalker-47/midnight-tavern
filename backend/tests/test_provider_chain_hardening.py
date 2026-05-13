"""Phase 1 hardening tests for ``ProviderChain``.

Covers:
* Validation-triggered fallback — provider A returns invalid bytes (HTML error
  page magic) → chain advances to provider B → success.
* Per-provider timeout fallback — provider A hangs past the configured
  ``timeout_s`` → chain advances to provider B.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.modules.images.providers.base import ImageProviderError
from app.modules.images.providers.chain import (
    AllProvidersFailedError,
    ProviderChain,
)

# 1x1 transparent PNG, magic bytes start with 89 50 4e 47.
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xfc\xcf"
    b"\xc0\xc0\xc0\xc0\xc0\xc0\xc0\x00\x07\x00\x03\x00\x01\x83/\xa2j\x00"
    b"\x00\x00\x00IEND\xaeB`\x82"
)


class _HtmlBytesProvider:
    """Returns an HTML error page disguised as image bytes."""

    name = "html_returner"

    async def generate(self, *_a: Any, **_kw: Any) -> bytes:
        return b"<!DOCTYPE html><html><body>403 forbidden</body></html>"


class _HangingProvider:
    name = "hanger"

    def __init__(self, sleep_s: float = 5.0) -> None:
        self.sleep_s = sleep_s

    async def generate(self, *_a: Any, **_kw: Any) -> bytes:
        await asyncio.sleep(self.sleep_s)
        return _TINY_PNG


class _RaisingProvider:
    name = "raiser"

    async def generate(self, *_a: Any, **_kw: Any) -> bytes:
        raise ImageProviderError("simulated transient failure", transient=True)


class _OkProvider:
    name = "ok"

    async def generate(self, *_a: Any, **_kw: Any) -> bytes:
        return _TINY_PNG


@pytest.mark.asyncio
async def test_chain_advances_on_invalid_image_bytes():
    chain = ProviderChain([_HtmlBytesProvider(), _OkProvider()], timeout_s=5)
    result = await chain.generate("p", "n", [], {"seed": 1, "steps": 10, "cfg_scale": 4, "width": 64, "height": 64})
    assert result.provider_used == "ok"
    assert result.providers_attempted == ["html_returner", "ok"]


@pytest.mark.asyncio
async def test_chain_advances_on_provider_timeout():
    # Hanger sleeps 5s; chain timeout is 0.2s → should advance.
    chain = ProviderChain([_HangingProvider(sleep_s=5.0), _OkProvider()], timeout_s=0)
    # ``timeout_s=0`` would be a degenerate case; use a tiny positive instead.
    chain.timeout_s = 1  # 1s ceiling, hanger sleeps 5s.
    chain.providers[0] = _HangingProvider(sleep_s=5.0)
    result = await chain.generate("p", "n", [], {"seed": 2, "steps": 10, "cfg_scale": 4, "width": 64, "height": 64})
    assert result.provider_used == "ok"
    assert "hanger" in result.providers_attempted


@pytest.mark.asyncio
async def test_chain_advances_on_provider_error():
    chain = ProviderChain([_RaisingProvider(), _OkProvider()], timeout_s=5)
    result = await chain.generate("p", "n", [], {"seed": 3, "steps": 10, "cfg_scale": 4, "width": 64, "height": 64})
    assert result.provider_used == "ok"


@pytest.mark.asyncio
async def test_chain_raises_when_all_fail():
    chain = ProviderChain([_HtmlBytesProvider(), _RaisingProvider()], timeout_s=5)
    with pytest.raises(AllProvidersFailedError) as exc:
        await chain.generate("p", "n", [], {"seed": 4, "steps": 10, "cfg_scale": 4, "width": 64, "height": 64})
    assert exc.value.attempted == ["html_returner", "raiser"]
