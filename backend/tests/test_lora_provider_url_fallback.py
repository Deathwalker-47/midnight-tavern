"""Phase 1 LoRA provider URL fallback tests.

A LoRA stored under Runware shorthand (``deathwalker:slug@1``) must be either
substituted via ``runware_lora_mapping.json`` reverse lookup OR dropped (with
a warning) when sent to a non-Runware provider. We never pass an unusable URL
to fal/wavespeed/together.
"""

from __future__ import annotations

import json

import pytest

from app.modules.images import lora as lora_module
from app.modules.images.lora import LoRAManager


def _matched_loras():
    return [
        {
            "id": "hosted_only",
            "data": {
                "name": "Hosted only",
                "url": "deathwalker:nimya@1",  # Runware shorthand
                "weight": 0.8,
                "category": "character",
            },
            "reason": "keyword:nimya",
        },
        {
            "id": "public",
            "data": {
                "name": "Public",
                "url": "https://example.com/realism.safetensors",
                "weight": 0.3,
                "category": "general",
            },
            "reason": "permanent",
        },
    ]


def test_runware_provider_keeps_shorthand_url():
    out = LoRAManager.build_lora_list(_matched_loras(), max_loras=5, provider_name="runware")
    urls = [item["url"] for item in out]
    assert "deathwalker:nimya@1" in urls
    assert "https://example.com/realism.safetensors" in urls


def test_non_runware_drops_unmapped_shorthand_url(monkeypatch):
    lora_module.reset_runware_mapping_for_tests()
    monkeypatch.setattr(lora_module, "_load_runware_mapping", lambda: {})
    out = LoRAManager.build_lora_list(_matched_loras(), max_loras=5, provider_name="wavespeed")
    ids = [item["id"] for item in out]
    assert "hosted_only" not in ids
    assert "public" in ids


def test_non_runware_substitutes_when_mapping_present(monkeypatch):
    lora_module.reset_runware_mapping_for_tests()
    monkeypatch.setattr(
        lora_module,
        "_load_runware_mapping",
        lambda: {"deathwalker:nimya@1": "https://hf.co/anujithc/nimya.safetensors"},
    )
    out = LoRAManager.build_lora_list(_matched_loras(), max_loras=5, provider_name="fal")
    mapped = next(item for item in out if item["id"] == "hosted_only")
    assert mapped["url"] == "https://hf.co/anujithc/nimya.safetensors"


def test_default_call_without_provider_name_keeps_legacy_behavior():
    """Existing callers that don't pass ``provider_name`` still get every URL."""
    out = LoRAManager.build_lora_list(_matched_loras(), max_loras=5)
    assert {item["id"] for item in out} == {"hosted_only", "public"}


def test_mapping_loader_reads_file(tmp_path, monkeypatch):
    mapping_file = tmp_path / "runware_lora_mapping.json"
    mapping_file.write_text(json.dumps({"mappings": {"x:y@1": "https://e.com/x.safetensors"}}))
    monkeypatch.setattr(lora_module, "_runware_mapping_path", lambda: mapping_file)
    lora_module.reset_runware_mapping_for_tests()
    assert lora_module._load_runware_mapping() == {"x:y@1": "https://e.com/x.safetensors"}


def test_missing_mapping_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(lora_module, "_runware_mapping_path", lambda: tmp_path / "does_not_exist.json")
    lora_module.reset_runware_mapping_for_tests()
    assert lora_module._load_runware_mapping() == {}
