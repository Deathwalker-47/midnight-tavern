"""Tier selection + ``[SCENE_BEAT:*]`` marker handling.

The story LLM is encouraged to emit one of:

* ``[SCENE_BEAT:single]`` — Tier 2 (single-character) image
* ``[SCENE_BEAT:multi]``  — Tier 3 (cached composite)
* ``[SCENE_BEAT:none]``   — explicit no-image moment

Behavior priority:

1. ``user_pref == "never"`` → always Tier 1 (no image), regardless of marker.
2. Explicit marker present → use it, even when ``user_pref == "always"``
   (markers are higher-fidelity than coarse heuristics).
3. ``user_pref == "always"`` → fall back to Tier 2 or Tier 3 depending on
   active character count.
4. Otherwise heuristic — look for "they walk into", "later that night",
   new-character introductions, etc. — and default to Tier 1.

This module is intentionally framework-free so it can be unit-tested without
spinning up FastAPI / Postgres / Anthropic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# Marker syntax: ``[SCENE_BEAT:single|multi|none]``. Tolerant of surrounding
# whitespace and case.
_MARKER_RE = re.compile(r"\[SCENE_BEAT\s*:\s*(single|multi|none)\]", re.IGNORECASE)


class Tier(str, Enum):
    none = "none"          # Tier 1
    single = "single"      # Tier 2
    multi = "multi"        # Tier 3


class ImagePref(str, Enum):
    auto = "auto"
    always = "always"
    never = "never"


@dataclass
class TierDecision:
    tier: Tier
    source: str  # "marker" | "user_pref" | "heuristic" | "default"
    stripped_text: str


# Heuristic location/scene-change keywords.
_SCENE_HEURISTICS = (
    "they walk into",
    "they step into",
    "they enter",
    "later that night",
    "later that day",
    "the next morning",
    "the next day",
    "hours later",
    "back at",
    "arrives at",
    "outside the",
    "inside the",
)


def parse_markers(text: str) -> tuple[Tier | None, str]:
    """Return ``(marker_tier_or_None, text_with_markers_stripped)``.

    If multiple markers appear, the last one wins (LLM may emit a stray one
    early in a response, but the final ruling is what matters). Markers are
    stripped from the returned text along with any adjacent whitespace.
    """
    matches = list(_MARKER_RE.finditer(text))
    cleaned = _MARKER_RE.sub(" ", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"  +", " ", cleaned).strip()
    if not matches:
        return None, cleaned

    last = matches[-1].group(1).lower()
    if last == "none":
        return Tier.none, cleaned
    if last == "single":
        return Tier.single, cleaned
    if last == "multi":
        return Tier.multi, cleaned
    return None, cleaned


def select_tier(
    text: str,
    *,
    active_character_count: int,
    user_pref: ImagePref = ImagePref.auto,
) -> TierDecision:
    """Choose the tier for a message based on marker, pref, and heuristics."""
    marker_tier, stripped = parse_markers(text)

    if user_pref == ImagePref.never:
        return TierDecision(tier=Tier.none, source="user_pref", stripped_text=stripped)

    if marker_tier is not None:
        return TierDecision(tier=marker_tier, source="marker", stripped_text=stripped)

    if user_pref == ImagePref.always:
        forced = Tier.multi if active_character_count >= 2 else Tier.single
        return TierDecision(tier=forced, source="user_pref", stripped_text=stripped)

    # Heuristic — Tier 2 on obvious scene-change / introduction cues.
    lower = stripped.lower()
    if any(hint in lower for hint in _SCENE_HEURISTICS):
        return TierDecision(tier=Tier.single, source="heuristic", stripped_text=stripped)

    return TierDecision(tier=Tier.none, source="default", stripped_text=stripped)


# ── Streaming-aware marker stripper ─────────────────────────────────────────


class MarkerStreamStripper:
    """Buffer a streaming token feed and yield text with markers removed.

    The LLM may emit a marker across multiple SSE chunks (``[SCENE_BEAT:`` in
    one, ``multi]`` in the next). This stripper keeps a small tail buffer so
    incomplete markers don't leak through; complete markers are recorded and
    stripped before emission.

    Usage::

        stripper = MarkerStreamStripper()
        async for token in provider.stream(...):
            safe = stripper.feed(token)
            if safe:
                yield safe
        final = stripper.flush()
        if final:
            yield final
        tier = stripper.consumed_marker  # Tier | None
    """

    # Longest marker form: ``[SCENE_BEAT:single]`` is 19 chars; ``multi`` 18;
    # ``none`` 17. Buffer at least 24 to be safe.
    _BUFFER_TAIL = 28

    def __init__(self) -> None:
        self._buffer = ""
        self._consumed: Tier | None = None

    @property
    def consumed_marker(self) -> Tier | None:
        return self._consumed

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        self._flush_complete_markers()
        # Emit everything except the tail (which might still be a partial marker).
        if len(self._buffer) <= self._BUFFER_TAIL:
            return ""
        emit = self._buffer[: -self._BUFFER_TAIL]
        self._buffer = self._buffer[-self._BUFFER_TAIL:]
        return emit

    def flush(self) -> str:
        self._flush_complete_markers()
        out = self._buffer
        self._buffer = ""
        return out

    def _flush_complete_markers(self) -> None:
        while True:
            match = _MARKER_RE.search(self._buffer)
            if not match:
                break
            tag = match.group(1).lower()
            if tag == "single":
                self._consumed = Tier.single
            elif tag == "multi":
                self._consumed = Tier.multi
            elif tag == "none":
                self._consumed = Tier.none
            # Strip the marker out (replace with a single space so surrounding
            # text doesn't lose word boundaries).
            self._buffer = (
                self._buffer[: match.start()] + " " + self._buffer[match.end():]
            )


SYSTEM_PROMPT_APPENDIX = """
\n## Image generation markers
When the moment in the narrative would clearly benefit from an illustration, emit ONE of these literal markers in your response (and only one):
* `[SCENE_BEAT:single]` — close-up of a single character (new entrance, key emotional beat).
* `[SCENE_BEAT:multi]` — group/scene shot with two or more characters present.
* `[SCENE_BEAT:none]` — explicit no-image (silent or purely dialogue moment).
Most messages should have NO marker. Use markers sparingly; one image every 3-5 turns is typical.
Markers are stripped from the user-visible message; don't surround them with explanation.
""".strip()
