"""Tests for the scene classifier — heuristic path + filter-on-unknown."""

from __future__ import annotations

import pytest

from app.modules.images.scene_classifier import (
    KNOWN_BACKDROP_TAGS,
    PoseRecommendation,
    SceneClassification,
    classify_scene,
    heuristic_classify,
    _filter_classification,
)


def test_heuristic_picks_tavern_warm():
    out = heuristic_classify(
        "They sit by the fire in a warm tavern, sharing wine.",
        ["c1", "c2"],
    )
    assert "tavern" in out.tags
    assert "warm-light" in out.tags
    assert out.method == "heuristic"
    assert [p.character_id for p in out.poses] == ["c1", "c2"]
    assert all(p.pose_slug == "sitting_relaxed" for p in out.poses)


def test_heuristic_picks_forest_night():
    out = heuristic_classify("They walk through a moonlit forest at night.", ["c1"])
    assert "forest" in out.tags
    assert "night" in out.tags
    assert out.poses[0].pose_slug == "walking_forward"


def test_heuristic_fight_scene():
    out = heuristic_classify("They draw their swords, ready to fight.", ["c1", "c2"])
    assert out.poses[0].pose_slug in {"fighting_stance", "holding_weapon"}


def test_heuristic_expression_angry():
    out = heuristic_classify("She glares at him with rage.", ["c1"])
    assert out.poses[0].expression_slug == "angry"


def test_heuristic_falls_back_to_indoor_when_no_hints():
    out = heuristic_classify("Hello.", ["c1"])
    assert "indoor" in out.tags


def test_filter_classification_drops_unknown_tags_and_poses():
    payload = {
        "tags": ["tavern", "made_up_tag", "warm-light"],
        "poses": [
            {
                "character_id": "c1",
                "pose_slug": "made_up_pose",
                "expression_slug": "happy",  # not in known set
                "facing": "forward",
            },
            {
                "character_id": "unknown_c",  # not in character list → dropped
                "pose_slug": "standing_neutral",
                "expression_slug": "neutral",
                "facing": "forward",
            },
        ],
    }
    out = _filter_classification(payload, ["c1"])
    assert out.tags == ["tavern", "warm-light"]
    assert len(out.poses) == 1
    assert out.poses[0].pose_slug == "standing_neutral"  # defaulted from unknown
    assert out.poses[0].expression_slug == "neutral"


def test_filter_classification_fills_missing_characters():
    payload = {"tags": ["forest"], "poses": []}
    out = _filter_classification(payload, ["c1", "c2"])
    assert {p.character_id for p in out.poses} == {"c1", "c2"}


@pytest.mark.asyncio
async def test_classify_scene_falls_back_to_heuristic_without_anthropic(monkeypatch):
    from app.core import config as config_mod

    monkeypatch.setattr(config_mod.settings, "ANTHROPIC_API_KEY", "", raising=False)
    out = await classify_scene("warm tavern at night", ["c1", "c2"])
    assert out.method == "heuristic"
    assert "tavern" in out.tags


def test_known_vocab_not_empty():
    assert "tavern" in KNOWN_BACKDROP_TAGS
    assert len(KNOWN_BACKDROP_TAGS) >= 30
