"""Validate that the shipped backdrop and pose-matrix YAML files parse and
contain the structure the workers expect. These act as contract tests so
that hand-editing the YAML doesn't quietly break the worker."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def backdrop_specs():
    with (ROOT / "config" / "backdrop_specs.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def pose_matrix():
    with (ROOT / "config" / "pose_matrix.yaml").open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_backdrop_specs_shape(backdrop_specs):
    assert isinstance(backdrop_specs, dict)
    assert "backdrops" in backdrop_specs
    items = backdrop_specs["backdrops"]
    assert len(items) >= 25, "want at least 25 backdrops in the v1 library"

    slugs = set()
    for spec in items:
        for key in ("slug", "prompt", "tags", "lighting_profile", "aspect_ratio"):
            assert key in spec, f"{spec.get('slug')!r} missing {key}"
        slug = spec["slug"]
        assert slug not in slugs, f"duplicate slug {slug!r}"
        slugs.add(slug)
        assert isinstance(spec["tags"], list) and spec["tags"]
        assert isinstance(spec["lighting_profile"], dict)


def test_pose_matrix_shape(pose_matrix):
    assert isinstance(pose_matrix, dict)
    assert "poses" in pose_matrix and "expressions" in pose_matrix
    poses = pose_matrix["poses"]
    expressions = pose_matrix["expressions"]
    assert len(poses) >= 15
    assert len(expressions) >= 4

    for pose in poses:
        for key in ("slug", "prompt_fragment", "facings"):
            assert key in pose, f"pose {pose.get('slug')!r} missing {key}"
        assert "{trigger}" in pose["prompt_fragment"]
        assert isinstance(pose["facings"], list) and pose["facings"]

    for expr in expressions:
        for key in ("slug", "prompt_fragment"):
            assert key in expr


def test_pose_matrix_generation_section(pose_matrix):
    gen = pose_matrix.get("generation", {})
    assert gen.get("width", 0) >= 512
    assert gen.get("height", 0) >= 512
