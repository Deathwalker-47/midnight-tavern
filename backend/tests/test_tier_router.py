"""Tests for the SCENE_BEAT marker parser, streaming stripper, and tier
router decision logic."""

from __future__ import annotations

import pytest

from app.modules.images.tier_router import (
    ImagePref,
    MarkerStreamStripper,
    Tier,
    parse_markers,
    select_tier,
)


# ── parse_markers ─────────────────────────────────────────────────────────


def test_parse_no_marker():
    tier, text = parse_markers("she walks into the tavern.")
    assert tier is None
    assert text == "she walks into the tavern."


def test_parse_single():
    tier, text = parse_markers("she enters the inn. [SCENE_BEAT:single]")
    assert tier is Tier.single
    assert "[SCENE_BEAT" not in text
    assert text.endswith("inn.")


def test_parse_multi():
    tier, text = parse_markers("[SCENE_BEAT:multi] the two of them sit by the fire.")
    assert tier is Tier.multi


def test_parse_none():
    tier, _ = parse_markers("[SCENE_BEAT:none] silence falls.")
    assert tier is Tier.none


def test_parse_case_insensitive_and_whitespace_tolerant():
    tier, _ = parse_markers("hi [SCENE_BEAT : Single]")
    assert tier is Tier.single


def test_parse_multiple_markers_last_wins():
    tier, _ = parse_markers("a [SCENE_BEAT:single] b [SCENE_BEAT:multi]")
    assert tier is Tier.multi


# ── select_tier ───────────────────────────────────────────────────────────


def test_select_marker_wins_over_user_always():
    d = select_tier(
        "[SCENE_BEAT:none] dialogue",
        active_character_count=2,
        user_pref=ImagePref.always,
    )
    assert d.tier is Tier.none
    assert d.source == "marker"


def test_select_never_overrides_marker():
    d = select_tier(
        "[SCENE_BEAT:multi] big scene",
        active_character_count=3,
        user_pref=ImagePref.never,
    )
    assert d.tier is Tier.none
    assert d.source == "user_pref"


def test_select_user_always_picks_multi_when_chars_2plus():
    d = select_tier("no marker", active_character_count=2, user_pref=ImagePref.always)
    assert d.tier is Tier.multi


def test_select_user_always_picks_single_for_solo():
    d = select_tier("no marker", active_character_count=1, user_pref=ImagePref.always)
    assert d.tier is Tier.single


def test_select_heuristic_triggers_on_scene_change():
    d = select_tier(
        "Later that night, they walk into the dungeon.",
        active_character_count=1,
    )
    assert d.tier is Tier.single
    assert d.source == "heuristic"


def test_select_default_is_none():
    d = select_tier("they chat quietly.", active_character_count=1)
    assert d.tier is Tier.none
    assert d.source == "default"


# ── MarkerStreamStripper ─────────────────────────────────────────────────


def test_stream_stripper_no_marker():
    s = MarkerStreamStripper()
    out = []
    for chunk in ("hello ", "world!"):
        out.append(s.feed(chunk))
    out.append(s.flush())
    assert "".join(out) == "hello world!"
    assert s.consumed_marker is None


def test_stream_stripper_marker_within_one_chunk():
    s = MarkerStreamStripper()
    out = []
    for chunk in ("She smiled. [SCENE_BEAT:single] The end.",):
        out.append(s.feed(chunk))
    out.append(s.flush())
    visible = "".join(out)
    assert "[SCENE_BEAT" not in visible
    assert "She smiled" in visible and "The end" in visible
    assert s.consumed_marker is Tier.single


def test_stream_stripper_marker_split_across_chunks():
    s = MarkerStreamStripper()
    # Split such that "[SCENE_BEAT:multi]" arrives across three chunks.
    chunks = ["Big moment. [SCEN", "E_BEAT:mul", "ti] They embrace."]
    out = [s.feed(c) for c in chunks]
    out.append(s.flush())
    visible = "".join(out)
    assert "[SCENE_BEAT" not in visible
    assert "Big moment" in visible
    assert "They embrace" in visible
    assert s.consumed_marker is Tier.multi


def test_stream_stripper_multiple_markers_last_wins():
    s = MarkerStreamStripper()
    out = [s.feed("a [SCENE_BEAT:single] b [SCENE_BEAT:multi] c")]
    out.append(s.flush())
    assert s.consumed_marker is Tier.multi
    assert "[SCENE_BEAT" not in "".join(out)
