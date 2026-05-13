"""Phase 1 master_lora_dict audit verification.

Per the three-tier image plan, only ``realism`` should be a permanent LoRA.
The 6 other previously-permanent flags (imagination, detail_enhancer,
detailed_hands, indian_style_face, nsfw_master_mystic, nsfw_busts) must be
keyword-triggered instead so they no longer consume role caps unconditionally.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DICT_PATH = Path(__file__).resolve().parents[1] / "data" / "master_lora_dict.json"


@pytest.fixture(scope="module")
def lora_dict():
    with DICT_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_permanent_loras_is_realism_only(lora_dict):
    assert lora_dict["config"]["permanent_loras"] == ["realism"]


@pytest.mark.parametrize(
    "lora_id",
    [
        "imagination",
        "detail_enhancer",
        "detailed_hands",
        "indian_style_face",
        "nsfw_master_mystic",
        "nsfw_busts",
    ],
)
def test_previously_permanent_now_keyword_triggered(lora_dict, lora_id):
    entry = lora_dict["loras"][lora_id]
    assert entry["permanent"] is False, f"{lora_id} should no longer be permanent"
    assert entry["keywords"], f"{lora_id} needs at least one trigger keyword"


def test_realism_still_permanent(lora_dict):
    assert lora_dict["loras"]["realism"]["permanent"] is True
