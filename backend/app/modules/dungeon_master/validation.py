"""Stat delta validation and death state detection.

Runs before any LLM-proposed stat changes are written to the database.
The model proposes; this code validates, clamps, and executes atomically.
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
    delta: Any | None  # None for text/enum stats (full replacement, not delta)


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
) -> ValidationResult:
    """Apply DM-proposed stat deltas to current stats.

    - Number stats: deltas accumulate. 0 resets to initial_value.
    - Text/enum stats: value is replaced outright.
    - Values are clamped to schema bounds (no rejection, just clamping).
    - Death state is detected when an HP-like stat reaches 0 or below.
    - Unknown stat names are collected but not applied.
    """
    schema_map = {s["name"]: s for s in stat_schema}
    new_stats = dict(current_stats)
    result = ValidationResult(new_stats=new_stats)

    for stat_name, delta in deltas.items():
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

            # Convert back to int if there's no fractional part
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
