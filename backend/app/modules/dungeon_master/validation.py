"""Stat delta validation and death state detection.

Runs before any LLM-proposed stat changes are written to the database.
The model proposes; this code validates, clamps, and executes atomically.

Two modes for number stats (per reference doc §3.3):
  delta        — relative change from current value (combat, XP, gold)
  absolute_values — set an exact value (character creation, class assignment, level-up)
"""

from dataclasses import dataclass, field
from typing import Any

_DEATH_STAT_NAMES = frozenset({"hp", "health", "hit_points", "hitpoints", "life", "lives"})


@dataclass
class StatChange:
    name: str
    display_name: str
    old_value: Any
    new_value: Any
    delta: Any | None  # None for text/enum stats and absolute_values
    was_absolute: bool = False  # True when set via absolute_values (not delta)


@dataclass
class ValidationResult:
    new_stats: dict[str, Any]
    changes: list[StatChange] = field(default_factory=list)
    death_state: bool = False
    unknown_stats: list[str] = field(default_factory=list)
    clamped_stats: list[str] = field(default_factory=list)


def apply_stat_deltas(
    stat_schema: list[dict[str, Any]],
    current_stats: dict[str, Any],
    deltas: dict[str, Any],
    absolute_values: dict[str, Any] | None = None,
) -> ValidationResult:
    """Apply DM-proposed stat deltas (and optional absolute overrides) to current stats.

    deltas:
      - Number stats: deltas accumulate. 0 resets to initial_value (full heal).
      - Text/enum stats: value is replaced outright (delta IS the new value).

    absolute_values (optional):
      - Number stats only. Sets the stat to exactly this value regardless of current.
      - Used for character creation (set STR=14), level-up HP base increases, etc.
      - Wins over any delta for the same stat.

    Values are clamped to schema bounds. Unknown stat names are collected but not applied.
    Death state is detected when an HP-like stat reaches ≤ 0.
    """
    schema_map = {s["name"]: s for s in stat_schema}
    new_stats = dict(current_stats)
    result = ValidationResult(new_stats=new_stats)
    abs_vals = absolute_values or {}

    # Process absolute_values first (character creation / level-up overrides)
    for stat_name, abs_value in abs_vals.items():
        if stat_name not in schema_map:
            result.unknown_stats.append(stat_name)
            continue
        stat_def = schema_map[stat_name]
        display = stat_def.get("display_name", stat_name)
        stat_type = stat_def.get("type", "number")
        old_value = current_stats.get(stat_name, stat_def.get("initial_value", 0))

        if stat_type == "number":
            new_value = float(abs_value)
            min_v = stat_def.get("min")
            max_v = stat_def.get("max")
            if min_v is not None and new_value < min_v:
                new_value = min_v
                result.clamped_stats.append(stat_name)
            if max_v is not None and new_value > max_v:
                new_value = max_v
                result.clamped_stats.append(stat_name)
            if isinstance(new_value, float) and new_value == int(new_value):
                new_value = int(new_value)
            if stat_name.lower() in _DEATH_STAT_NAMES and new_value <= 0:
                result.death_state = True
            new_stats[stat_name] = new_value
            result.changes.append(StatChange(
                name=stat_name,
                display_name=display,
                old_value=old_value,
                new_value=new_value,
                delta=None,
                was_absolute=True,
            ))
        else:
            # Text/enum: treat like a direct set
            new_stats[stat_name] = abs_value
            result.changes.append(StatChange(
                name=stat_name, display_name=display,
                old_value=old_value, new_value=abs_value, delta=None, was_absolute=True,
            ))

    # Process deltas — skip stats already handled by absolute_values
    for stat_name, delta in deltas.items():
        if stat_name in abs_vals:
            continue  # absolute_values wins
        if stat_name not in schema_map:
            result.unknown_stats.append(stat_name)
            continue

        stat_def = schema_map[stat_name]
        display = stat_def.get("display_name", stat_name)
        stat_type = stat_def.get("type", "number")
        initial = stat_def.get("initial_value", 0)
        old_value = current_stats.get(stat_name, initial)

        if stat_type == "number":
            new_value = initial if delta == 0 else float(old_value) + float(delta)

            min_v = stat_def.get("min")
            max_v = stat_def.get("max")
            if min_v is not None and new_value < min_v:
                new_value = min_v
                result.clamped_stats.append(stat_name)
            if max_v is not None and new_value > max_v:
                new_value = max_v
                result.clamped_stats.append(stat_name)

            if isinstance(new_value, float) and new_value == int(new_value):
                new_value = int(new_value)

            if stat_name.lower() in _DEATH_STAT_NAMES and new_value <= 0:
                result.death_state = True

            new_stats[stat_name] = new_value
            result.changes.append(StatChange(
                name=stat_name,
                display_name=display,
                old_value=old_value,
                new_value=new_value,
                delta=delta,
            ))

        elif stat_type == "enum":
            options = stat_def.get("options", [])
            if delta not in options:
                result.unknown_stats.append(f"{stat_name}={delta!r}")
                continue
            new_stats[stat_name] = delta
            result.changes.append(StatChange(
                name=stat_name, display_name=display,
                old_value=old_value, new_value=delta, delta=None,
            ))

        elif stat_type == "text":
            new_stats[stat_name] = delta
            result.changes.append(StatChange(
                name=stat_name, display_name=display,
                old_value=old_value, new_value=delta, delta=None,
            ))

    return result


def initialize_stats(stat_schema: list[dict[str, Any]]) -> dict[str, Any]:
    """Build an initial stats dict from a ruleset's stat schema."""
    return {s["name"]: s.get("initial_value", 0) for s in stat_schema}


def verify_death_legitimate(
    stat_schema: list[dict[str, Any]],
    stats: dict[str, Any],
) -> bool:
    """Return True ONLY if at least one HP-like stat is actually at or below 0.

    Code-side guard against DM model inventing kill mechanics at 30+ HP.
    """
    schema_map = {s["name"]: s for s in stat_schema}
    for stat_name, value in stats.items():
        if stat_name.lower() not in _DEATH_STAT_NAMES:
            continue
        if not isinstance(value, (int, float)):
            continue
        stat_def = schema_map.get(stat_name, {})
        min_v = stat_def.get("min", 0)
        # Death is only legitimate when HP is at or below its minimum (typically 0)
        if value <= min_v:
            return True
    return False
