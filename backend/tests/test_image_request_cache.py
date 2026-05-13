"""Phase 1 request-level image cache tests.

Covers ``make_cache_key`` skip rules and the ``InMemoryImageCache`` LRU.
"""

from __future__ import annotations

import pytest

from app.modules.images.cache import (
    InMemoryImageCache,
    make_cache_key,
    reset_image_cache_for_tests,
)


def _params(seed=42):
    return {"seed": seed, "steps": 20, "cfg_scale": 4, "width": 1024, "height": 1024}


def test_random_seed_skips_cache():
    assert make_cache_key("p", "n", [], {**_params(seed=-1)}) is None


def test_missing_seed_skips_cache():
    assert make_cache_key("p", "n", [], {"steps": 20, "cfg_scale": 4, "width": 1024, "height": 1024}) is None


def test_deterministic_seed_produces_stable_key():
    a = make_cache_key("p", "n", [], _params())
    b = make_cache_key("p", "n", [], _params())
    assert a == b and a is not None and a.startswith("mt:img:")


def test_keys_differ_on_prompt_or_loras_or_params():
    base = make_cache_key("p", "n", [], _params())
    assert base != make_cache_key("p2", "n", [], _params())
    assert base != make_cache_key("p", "n2", [], _params())
    assert base != make_cache_key("p", "n", [{"url": "u1", "weight": 1.0}], _params())
    assert base != make_cache_key("p", "n", [], _params(seed=43))


def test_lora_order_does_not_affect_key():
    loras_a = [{"url": "u1", "weight": 1.0}, {"url": "u2", "weight": 0.5}]
    loras_b = [{"url": "u2", "weight": 0.5}, {"url": "u1", "weight": 1.0}]
    assert make_cache_key("p", "n", loras_a, _params()) == make_cache_key("p", "n", loras_b, _params())


@pytest.mark.asyncio
async def test_in_memory_cache_get_set_and_lru_eviction():
    reset_image_cache_for_tests()
    cache = InMemoryImageCache(max_entries=2)
    await cache.set("a", "url_a")
    await cache.set("b", "url_b")
    assert await cache.get("a") == "url_a"
    # Access of "a" makes "b" the LRU; inserting "c" should evict "b".
    await cache.set("c", "url_c")
    assert await cache.get("b") is None
    assert await cache.get("a") == "url_a"
    assert await cache.get("c") == "url_c"
