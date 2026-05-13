"""LLM-based scene classifier with deterministic fallback.

For a Tier 3 composite request the orchestrator needs:

* A set of backdrop tags (e.g. ``["indoor", "tavern", "warm-light"]``) to look
  up matching backdrops in Postgres.
* Per-character pose recommendations: ``{pose_slug, expression_slug, facing}``.

If an Anthropic API key is configured we call Claude Haiku (fast, cheap) with
a structured prompt and parse JSON out. Otherwise we fall back to a keyword
extractor — coarse but always works and keeps tests hermetic.

The classifier is intentionally lenient: it filters its output against the
known backdrop tag vocabulary and known pose/expression/facing slugs so a
hallucinated label can't crash the runtime lookup.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


# ── Vocabularies ────────────────────────────────────────────────────────────


KNOWN_BACKDROP_TAGS: set[str] = {
    "indoor", "outdoor", "tavern", "inn", "bedroom", "forest", "mountain",
    "meadow", "town", "harbor", "throne", "library", "dungeon", "temple",
    "workshop", "cave", "ruins", "desert", "village", "ship", "cafe",
    "garden", "beach", "ballroom", "kitchen",
    "warm-light", "cool-light", "low-light", "golden-hour", "daylight",
    "night", "dawn", "dusk", "morning", "sunset", "moonlit",
    "cozy", "intimate", "formal", "opulent", "dramatic", "atmospheric",
    "mysterious", "peaceful", "ominous", "magical", "ancient", "scholarly",
    "civic", "casual", "romantic", "vast", "vista", "water", "sea", "snow",
    "fantasy", "urban",
}

KNOWN_POSE_SLUGS: set[str] = {
    "standing_neutral", "standing_arms_crossed", "standing_one_hip",
    "sitting_relaxed", "sitting_leaning_forward",
    "leaning_against_wall", "kneeling_one_knee", "reaching_forward",
    "gesturing_open_palm", "hand_on_chin", "looking_over_shoulder",
    "walking_forward", "turning_away", "hands_clasped",
    "arms_outstretched_welcoming", "fighting_stance", "holding_weapon",
    "lying_propped", "close_portrait", "dancing_step",
}

KNOWN_EXPRESSIONS: set[str] = {"neutral", "smiling", "angry", "surprised", "sad"}

KNOWN_FACINGS: set[str] = {"forward", "three_quarter_left", "three_quarter_right"}


# ── Result types ───────────────────────────────────────────────────────────


@dataclass
class PoseRecommendation:
    character_id: str
    pose_slug: str
    expression_slug: str
    facing: str


@dataclass
class SceneClassification:
    tags: list[str]
    poses: list[PoseRecommendation]
    method: str  # "llm" | "heuristic"


# ── Heuristic fallback ─────────────────────────────────────────────────────


_INDOOR_HINTS = (
    "tavern", "inn", "bedroom", "throne", "library", "dungeon", "temple",
    "workshop", "kitchen", "cafe", "ballroom", "house", "room", "hall",
)
_OUTDOOR_HINTS = (
    "forest", "mountain", "meadow", "harbor", "beach", "garden", "desert",
    "village", "ship", "outside", "outdoors", "field",
)
_NIGHT_HINTS = ("night", "moonlit", "dark", "stars", "candle", "lantern")
_DAY_HINTS = ("morning", "noon", "daylight", "sun", "sunlight")
_DUSK_HINTS = ("dusk", "sunset", "golden hour", "evening")
_DAWN_HINTS = ("dawn", "sunrise")
_WARM_HINTS = ("warm", "fire", "hearth", "candle", "torch", "fireplace")
_COOL_HINTS = ("cold", "snow", "ice", "moon", "rain")
_MOOD_HINTS = {
    "intimate": ("intimate", "quiet", "alone", "private", "soft", "tender"),
    "dramatic": ("dramatic", "battle", "storm", "epic", "chase"),
    "peaceful": ("peaceful", "calm", "serene", "rest"),
    "mysterious": ("mysterious", "shadow", "whisper", "secret"),
    "ominous": ("ominous", "menacing", "ruin", "death", "blood"),
    "romantic": ("kiss", "embrace", "romantic", "blush", "blossom"),
    "magical": ("magic", "crystal", "spell", "glowing", "rune"),
}


def _heuristic_tags(description: str) -> list[str]:
    text = description.lower()
    tags: list[str] = []

    if any(h in text for h in _INDOOR_HINTS):
        tags.append("indoor")
    if any(h in text for h in _OUTDOOR_HINTS):
        tags.append("outdoor")
    # Location-specific tags.
    for marker in (
        "tavern", "inn", "bedroom", "forest", "mountain", "throne", "library",
        "dungeon", "temple", "kitchen", "cafe", "garden", "beach", "ballroom",
        "ship", "harbor", "village", "desert", "meadow", "town",
    ):
        if marker in text:
            tags.append(marker)
    # Time of day.
    if any(h in text for h in _DUSK_HINTS):
        tags.append("dusk")
    if any(h in text for h in _DAWN_HINTS):
        tags.append("dawn")
    if any(h in text for h in _NIGHT_HINTS):
        tags.append("night")
    if any(h in text for h in _DAY_HINTS):
        tags.append("daylight")
    if any(h in text for h in _WARM_HINTS):
        tags.append("warm-light")
    if any(h in text for h in _COOL_HINTS):
        tags.append("cool-light")
    # Mood.
    for tag, hints in _MOOD_HINTS.items():
        if any(h in text for h in hints):
            tags.append(tag)

    if not tags:
        # Genuine fallback: assume an unspecified indoor scene.
        tags = ["indoor"]

    # Filter and dedupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tags:
        if t in KNOWN_BACKDROP_TAGS and t not in seen:
            seen.add(t)
            deduped.append(t)
    return deduped


_POSE_KEYWORDS: dict[str, str] = {
    "stand": "standing_neutral",
    "sit": "sitting_relaxed",
    "lean": "leaning_against_wall",
    "kneel": "kneeling_one_knee",
    "walk": "walking_forward",
    "turn": "turning_away",
    "reach": "reaching_forward",
    "fight": "fighting_stance",
    "sword": "holding_weapon",
    "dance": "dancing_step",
    "ly": "lying_propped",
    "embrace": "arms_outstretched_welcoming",
    "look back": "looking_over_shoulder",
}


def _heuristic_pose(description: str) -> tuple[str, str, str]:
    text = description.lower()
    pose = "standing_neutral"
    for kw, slug in _POSE_KEYWORDS.items():
        if kw in text:
            pose = slug
            break
    expression = "neutral"
    if any(w in text for w in ("smile", "laugh", "grin", "happy")):
        expression = "smiling"
    elif any(w in text for w in ("angry", "fury", "rage", "glar")):
        expression = "angry"
    elif any(w in text for w in ("surprised", "shocked", "gasp")):
        expression = "surprised"
    elif any(w in text for w in ("sad", "weep", "cry", "tear")):
        expression = "sad"
    facing = "forward"
    return pose, expression, facing


def heuristic_classify(
    description: str, character_ids: list[str]
) -> SceneClassification:
    tags = _heuristic_tags(description)
    pose, expression, facing = _heuristic_pose(description)
    recs = [
        PoseRecommendation(
            character_id=cid,
            pose_slug=pose,
            expression_slug=expression,
            facing=facing,
        )
        for cid in character_ids
    ]
    return SceneClassification(tags=tags, poses=recs, method="heuristic")


# ── LLM-based classifier ───────────────────────────────────────────────────


_LLM_SYSTEM = """You classify roleplay scene descriptions into structured tags for image retrieval.

Return STRICT JSON with this schema:
{
  "tags": [string],
  "poses": [{"character_id": string, "pose_slug": string, "expression_slug": string, "facing": string}]
}

Choose tags only from: indoor, outdoor, tavern, inn, bedroom, forest, mountain, meadow, town, harbor, throne, library, dungeon, temple, workshop, cave, ruins, desert, village, ship, cafe, garden, beach, ballroom, kitchen, warm-light, cool-light, low-light, golden-hour, daylight, night, dawn, dusk, morning, sunset, moonlit, cozy, intimate, formal, opulent, dramatic, atmospheric, mysterious, peaceful, ominous, magical, ancient, scholarly, civic, casual, romantic, vast, vista, water, sea, snow, fantasy, urban.

Choose pose_slug only from: standing_neutral, standing_arms_crossed, standing_one_hip, sitting_relaxed, sitting_leaning_forward, leaning_against_wall, kneeling_one_knee, reaching_forward, gesturing_open_palm, hand_on_chin, looking_over_shoulder, walking_forward, turning_away, hands_clasped, arms_outstretched_welcoming, fighting_stance, holding_weapon, lying_propped, close_portrait, dancing_step.

Choose expression_slug only from: neutral, smiling, angry, surprised, sad.
Choose facing only from: forward, three_quarter_left, three_quarter_right.
Return JSON only, no commentary."""


def _filter_classification(payload: dict, character_ids: list[str]) -> SceneClassification:
    tags = [t for t in payload.get("tags", []) if t in KNOWN_BACKDROP_TAGS]
    poses_in = payload.get("poses", [])
    poses: list[PoseRecommendation] = []
    valid_char_ids = set(character_ids)
    for entry in poses_in:
        cid = str(entry.get("character_id", ""))
        if cid not in valid_char_ids:
            continue
        pose = entry.get("pose_slug", "standing_neutral")
        expr = entry.get("expression_slug", "neutral")
        facing = entry.get("facing", "forward")
        poses.append(
            PoseRecommendation(
                character_id=cid,
                pose_slug=pose if pose in KNOWN_POSE_SLUGS else "standing_neutral",
                expression_slug=expr if expr in KNOWN_EXPRESSIONS else "neutral",
                facing=facing if facing in KNOWN_FACINGS else "forward",
            )
        )
    # Fill missing characters with defaults.
    seen = {p.character_id for p in poses}
    for cid in character_ids:
        if cid not in seen:
            poses.append(
                PoseRecommendation(
                    character_id=cid,
                    pose_slug="standing_neutral",
                    expression_slug="neutral",
                    facing="forward",
                )
            )
    return SceneClassification(tags=tags or ["indoor"], poses=poses, method="llm")


async def classify_scene(
    description: str,
    character_ids: list[str],
    *,
    anthropic_client: object | None = None,
) -> SceneClassification:
    """Classify scene description → tags + per-character pose recommendations.

    Uses Claude Haiku if an Anthropic key is configured; otherwise falls back
    to a deterministic keyword heuristic. ``anthropic_client`` lets callers
    inject a mock for tests.
    """
    if anthropic_client is None and not settings.ANTHROPIC_API_KEY:
        return heuristic_classify(description, character_ids)

    if anthropic_client is None:
        try:
            import anthropic  # type: ignore[import-not-found]

            anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scene_classifier_anthropic_unavailable", error=str(exc))
            return heuristic_classify(description, character_ids)

    user_prompt = (
        f"Scene description: {description}\n"
        f"Characters: {', '.join(character_ids)}\n"
        "Return strict JSON per the schema."
    )
    try:
        response = await anthropic_client.messages.create(  # type: ignore[attr-defined]
            model=settings.DM_DEFAULT_MODEL,
            max_tokens=400,
            system=_LLM_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("scene_classifier_llm_call_failed", error=str(exc))
        return heuristic_classify(description, character_ids)

    text_chunks = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            text_chunks.append(text)
    raw = "".join(text_chunks).strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        logger.warning("scene_classifier_no_json", raw=raw[:200])
        return heuristic_classify(description, character_ids)
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("scene_classifier_bad_json", error=str(exc), raw=raw[:200])
        return heuristic_classify(description, character_ids)

    return _filter_classification(payload, character_ids)
