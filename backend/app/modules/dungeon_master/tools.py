"""Dynamic Anthropic tool definition generation from game stat schemas.

The set_stats tool is generated at runtime from the ruleset's stat_schema,
so different game systems (D&D 5e, survival horror, custom) get properly
typed tool parameters with correct bounds — no hardcoded RPG assumptions.
"""

from typing import Any

_TOOL_ROLL_DICE: dict[str, Any] = {
    "name": "roll_dice",
    "description": (
        "Roll dice server-side for a skill check, attack roll, saving throw, or any random event. "
        "Results are cryptographically random and tamper-proof. "
        "Supported notation: 1d20, 2d6+3, 4d6kh3, 1d100, dF (FATE dice)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Dice expression, e.g. '1d20', '2d6+3', '4d6kh3', '1d100', 'dF'",
            },
            "context": {
                "type": "string",
                "description": "What this roll is for, e.g. 'Attack roll vs goblin' or 'Dexterity saving throw DC 15'",
            },
        },
        "required": ["expression", "context"],
    },
}

_TOOL_SET_INVENTORY: dict[str, Any] = {
    "name": "set_inventory",
    "description": "Replace a character's full inventory with the new list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "character_id": {
                "type": "string",
                "description": "ID of the character whose inventory to update",
            },
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Complete new inventory list (replaces existing)",
            },
            "context": {
                "type": "string",
                "description": "Why inventory changed, e.g. 'Found healing potion in chest'",
            },
        },
        "required": ["character_id", "items", "context"],
    },
}

_TOOL_SET_SKILLS: dict[str, Any] = {
    "name": "set_skills",
    "description": "Update character skill values. Only provide skills that changed.",
    "input_schema": {
        "type": "object",
        "properties": {
            "character_id": {
                "type": "string",
                "description": "ID of the character whose skills to update",
            },
            "skills": {
                "type": "object",
                "description": "Skill name to new value mapping. Only include changed skills.",
                "additionalProperties": True,
            },
            "context": {
                "type": "string",
                "description": "Why skills changed",
            },
        },
        "required": ["character_id", "skills", "context"],
    },
}

_TOOL_SUBMIT_RESULTS: dict[str, Any] = {
    "name": "submit_results",
    "description": (
        "TERMINAL ACTION — call this when all mechanics are resolved. "
        "This unlocks the story AI to continue the narrative. "
        "Provide a narrative_hint so the story AI knows what mechanically happened."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "Player-visible DM ruling, e.g. 'You roll a 17 — the lock clicks open.'",
            },
            "narrative_hint": {
                "type": "string",
                "description": (
                    "Private context for the story AI about the mechanical outcome, "
                    "e.g. 'Attack hit for 8 damage. Target has 4 HP remaining. Player is unharmed.'"
                ),
            },
            "roll_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of all rolls made during this resolution (from roll_dice results)",
            },
        },
        "required": ["summary", "narrative_hint"],
    },
}

_TOOL_ASK_PLAYER: dict[str, Any] = {
    "name": "ask_player",
    "description": (
        "TERMINAL ACTION — pause and ask the player a clarifying question. "
        "The story AI will NOT generate a response until the player answers. "
        "Use sparingly — only when the action is genuinely ambiguous."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The clarifying question to present to the player",
            },
        },
        "required": ["question"],
    },
}

_TOOL_REJECT_ACTION: dict[str, Any] = {
    "name": "reject_action",
    "description": (
        "TERMINAL ACTION — reject a player action that is impossible, breaks the rules, "
        "or cannot be resolved. The story AI will NOT generate a response. "
        "The player will see your reason directly."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Clear explanation of why the action is rejected",
            },
        },
        "required": ["reason"],
    },
}


def _build_set_stats_tool(stat_schema: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the set_stats tool dynamically from a ruleset's stat schema."""
    properties: dict[str, Any] = {
        "character_id": {
            "type": "string",
            "description": "ID of the character whose stats to update",
        },
        "context": {
            "type": "string",
            "description": "Why stats changed, e.g. 'Took 8 damage from goblin sword strike'",
        },
    }

    stat_labels: list[str] = []
    for stat in stat_schema:
        name = stat["name"]
        display = stat.get("display_name", name)
        stat_type = stat.get("type", "number")

        stat_desc = stat.get("description", "")
        desc_suffix = f" — {stat_desc}" if stat_desc else ""

        if stat_type == "number":
            min_v = stat.get("min")
            max_v = stat.get("max")
            bounds = f" (bounds: {min_v}–{max_v})" if min_v is not None and max_v is not None else ""
            properties[name] = {
                "type": "number",
                "description": (
                    f"Delta modifier for {display}{bounds}{desc_suffix}. "
                    "Use negative to decrease (e.g. -8 for 8 damage), positive to increase. "
                    "Use 0 to reset to initial value (full heal equivalent)."
                ),
            }
            stat_labels.append(f"{display} [delta]")

        elif stat_type == "text":
            properties[name] = {
                "type": "string",
                "description": f"New text value for {display}{desc_suffix}",
            }
            stat_labels.append(f"{display} [text]")

        elif stat_type == "enum":
            options = stat.get("options", [])
            properties[name] = {
                "type": "string",
                "enum": options,
                "description": f"New state for {display}{desc_suffix}. Options: {', '.join(options)}",
            }
            stat_labels.append(f"{display} [enum: {', '.join(options)}]")

    return {
        "name": "set_stats",
        "description": (
            "Update character stats. For numeric stats use DELTA modifiers, not absolute values. "
            f"Available stats: {'; '.join(stat_labels)}. "
            "Only include stats that actually changed."
        ),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": ["character_id", "context"],
        },
    }


def build_dm_tools(stat_schema: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the complete DM tool list from a ruleset's stat schema."""
    return [
        _TOOL_ROLL_DICE,
        _build_set_stats_tool(stat_schema),
        _TOOL_SET_INVENTORY,
        _TOOL_SET_SKILLS,
        _TOOL_SUBMIT_RESULTS,
        _TOOL_ASK_PLAYER,
        _TOOL_REJECT_ACTION,
    ]
