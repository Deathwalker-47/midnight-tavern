"""DM agent service — orchestrates the agentic tool-calling evaluation loop.

Flow per player action:
  1. Pre-validate (Python, deterministic) — hard rejects cost zero tokens
  2. Load session + character sheet + ruleset from DB
  3. Generate DM tools dynamically from stat schema
  4. Build system prompt (base + rules_text + reminder_text, all cached)
  5. Call DM model with tools via Anthropic API
  6. Process tool calls in an agentic loop until terminal action
  7. Store rolls + actions + GameEvent in DB atomically
  8. Yield SSE events as each tool call executes
  9. Return final DMEvalResult (includes narrative_context for story AI)

Game engine reference lessons applied:
  - absolute_values in set_stats for character creation / level-up (§3.3)
  - set_spells tool with add/remove/update actions (§3.5)
  - set_description tool — anti-drift identity anchor sent to story AI (§3.4)
  - verify_death_legitimate() — DM model cannot fabricate deaths at 30+ HP (§5.2)
  - Character description injected into story AI narrative context (§3.4)
  - Built-in enforcement checklist appended LAST in system prompt (§9)
  - LLM-aware injection routing: Claude → system prompt, other → user message (§1.3)
"""

import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import anthropic
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import AppError
from app.modules.dungeon_master.dice import parse_and_roll
from app.modules.dungeon_master.models import (
    CharacterSheet,
    DMAction,
    DMActionType,
    DMRoll,
    DMSession,
    GameEvent,
)
from app.modules.dungeon_master.pre_validator import PreValidationResult, PreValidator
from app.modules.dungeon_master.schemas import DMEvalResult, DMVerdict, StatChangeSummary
from app.modules.dungeon_master.tools import build_dm_tools
from app.modules.dungeon_master.validation import (
    apply_stat_deltas,
    initialize_stats,
    verify_death_legitimate,
)

logger = structlog.get_logger(__name__)

_MAX_TOOL_ITERATIONS = 15


# ── Narrative context injector ────────────────────────────────────────────────


def build_narrative_context(result: DMEvalResult, character_description: str | None = None) -> str:
    """Build the [DUNGEON MIND RULING] block injected into the narrative AI prompt.

    Returns an empty string for pure pass verdicts with no notable outcomes,
    so the narrative AI generates freely. For all other verdicts the ruling is
    authoritative and the narrative AI must reflect it.

    character_description (§3.4): if set, appended as an identity anchor that
    prevents character drift across long sessions.
    """
    if result.verdict == DMVerdict.continue_ and not result.stat_changes and not result.summary:
        # Even on a clean pass, still inject description if present
        if character_description:
            return (
                f"[CHARACTER IDENTITY — maintain consistently]\n{character_description}\n"
                "[END CHARACTER IDENTITY]"
            )
        return ""

    lines = ["[DUNGEON MIND RULING — AUTHORITATIVE, DO NOT OVERRIDE]"]

    if result.verdict == DMVerdict.reject:
        lines.append("Ruling: REJECT")
        if result.rejection_reason:
            lines.append(f"Rejection reason: {result.rejection_reason}")
        lines.append(
            "Write a narrative where this action FAILS. The character attempts it "
            "but it does not work. Describe the failure dramatically without mentioning "
            "dice, stats, or game mechanics."
        )

    elif result.verdict == DMVerdict.ask:
        lines.append("Ruling: CLARIFICATION NEEDED")
        if result.ask_question:
            lines.append(f"DM question for player: {result.ask_question}")
        lines.append("Do not advance the story. Have the scene pause, awaiting player input.")

    elif result.verdict == DMVerdict.continue_:
        lines.append("Ruling: APPROVED")
        if result.summary:
            lines.append(f"Mechanical outcome: {result.summary}")
        if result.narrative_hint:
            lines.append(f"Context for narration: {result.narrative_hint}")
        if result.stat_changes:
            lines.append("Stat changes (show effects narratively, never mention numbers):")
            for sc in result.stat_changes:
                lines.append(f"  - {sc.display_name}: {sc.old_value} → {sc.new_value}")
        if result.death_state:
            lines.append(
                "DEATH TRIGGERED: A character has died. Narrate their death dramatically. "
                "They cannot be revived."
            )

    lines.append(
        "Write narrative prose that reflects these outcomes. Do NOT mention dice rolls, "
        "numbers, stat values, DCs, or game mechanics directly."
    )
    lines.append("[END DUNGEON MIND RULING]")

    # Anti-drift: inject description after ruling block (§3.4)
    if character_description:
        lines.append(f"\n[CHARACTER IDENTITY — maintain consistently]\n{character_description}\n[END CHARACTER IDENTITY]")

    return "\n".join(lines)


def inject_narrative_context(
    narrative_context: str,
    provider_messages: list[dict[str, Any]],
    story_provider: str = "anthropic",
) -> tuple[str | None, list[dict[str, Any]]]:
    """LLM-aware injection router (§1.3).

    Claude (Anthropic): inject into system prompt — returned as extra_system_text.
    Other providers (Gemini, GPT): inject into the last user message content.

    Returns (extra_system_text | None, modified_messages).
    """
    if not narrative_context:
        return None, provider_messages

    if story_provider == "anthropic":
        # System prompt injection — returned to caller to append to system
        return narrative_context, provider_messages
    else:
        # User message injection for Gemini / GPT
        msgs = [m.copy() for m in provider_messages]
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") == "user":
                msgs[i] = dict(msgs[i])
                msgs[i]["content"] = f"{msgs[i]['content']}\n\n{narrative_context}"
                break
        return None, msgs


# ── Prompt construction ───────────────────────────────────────────────────────


def _build_enforcement_checklist(enforcement: dict[str, Any]) -> str:
    """Build a minimal mandatory checklist based on active enforcement config.

    Goes at the VERY END of the system prompt for maximum model attention (§9).
    """
    items = [
        "✓ Only update stats that ACTUALLY changed in this action.",
        "✓ Use DELTA values for stat changes, not absolute values (except in absolute_values dict).",
        "✓ Always end with exactly ONE terminal action: submit_results, ask_player, or reject_action.",
    ]
    if enforcement.get("permadeath", True):
        items.append(
            "✓ Death (is_alive=False) is ONLY valid when HP is at or below minimum. "
            "NEVER invent kill mechanics. HP > 0 = alive, no exceptions."
        )
    if enforcement.get("dead_cannot_act", True):
        items.append("✓ Dead characters CANNOT act. Reject any action from a dead character.")
    if enforcement.get("no_invented_powers", True):
        items.append(
            "✓ NEVER grant skills, spells, or feats not earned through level-up or rules. "
            "If you find yourself adding an ability mid-scene: STOP and reject instead."
        )
    if enforcement.get("resource_costs_enforced", True):
        items.append(
            "✓ Always deduct MP/resource costs when abilities are used. "
            "Always send stat_changes for currency — NEVER mention gold/silver without a stat_change."
        )
    if enforcement.get("require_skill_for_ability", True):
        items.append("✓ Reject abilities the character has not learned (not in their skill/spell list).")
    items.append(
        "✓ Use set_spells for spells, set_skills for skills. NEVER put a spell in set_skills or vice versa."
    )
    items.append(
        "✓ Use set_description whenever the character's appearance changes (new equipment, wounds, transformation)."
    )
    return "\n".join(f"  {item}" for item in items)


def _build_system_prompt_blocks(
    ruleset_name: str,
    stat_schema: list[dict[str, Any]],
    sheet: CharacterSheet,
    rules_text: str | None,
    reminder_text: str | None,
    enforcement_config: dict[str, Any],
    pre_validation_flags: str = "",
) -> list[dict[str, Any]]:
    """Build system prompt as content blocks with prompt caching on rules.

    Three potential cache layers:
      1. Base prompt (character-specific — not cached)
      2. rules_text (changes infrequently — cache_control: ephemeral)
      3. reminder_text + mandatory checklist (injected last — cache_control: ephemeral)

    Per §9 of the game engine reference: the MANDATORY CHECKLIST goes at the
    very end because LLMs pay highest attention to the end of their context.
    """
    stat_lines = "\n".join(
        f"  {name}: {value}" for name, value in (sheet.stats or {}).items()
    )
    inventory_str = (
        ", ".join(str(i) for i in sheet.inventory) if sheet.inventory else "Empty"
    )
    skills_str = (
        ", ".join(f"{k}:{v}" for k, v in (sheet.skills or {}).items()) or "None"
    )
    spells_str = "None"
    if sheet.spells:
        spells_str = ", ".join(
            f"{s['name']} (C{s.get('circle', '?')})" if isinstance(s, dict) else str(s)
            for s in sheet.spells
        )

    alive_str = "ALIVE" if sheet.is_alive else "DEAD — cannot act"
    description_section = ""
    if sheet.description:
        description_section = f"\nDescription: {sheet.description}"

    flags_section = ""
    if pre_validation_flags:
        flags_section = f"\n\n## Pre-Validation Flags\n{pre_validation_flags}"

    base_prompt = f"""\
You are the Dungeon Master for a roleplay session using the "{ruleset_name}" ruleset.

Your role: evaluate the player's latest action for mechanical implications, roll dice when required, update stats/inventory/skills/spells, and enforce game rules. The story AI handles all narrative — you handle ONLY game mechanics.

## Current Character Sheet
Character: {sheet.display_name or "Player"} [{alive_str}]{description_section}
Stats:
{stat_lines or "  (none defined)"}
Inventory: {inventory_str}
Skills: {skills_str}
Spells: {spells_str}

## Instructions
1. Determine whether the player's action requires a mechanical resolution.
2. If no mechanics apply, call submit_results immediately with an empty narrative_hint.
3. If mechanics apply: roll appropriate dice, apply stat changes, then call submit_results.
4. For number stats, use DELTA values (negative = decrease, positive = increase, 0 = reset to initial).
5. Use absolute_values in set_stats ONLY for character creation or level-up base increases.
6. Use set_spells (NOT set_skills) for spells. Use set_description when appearance changes.
7. Only update stats that actually changed.
8. Always end with exactly one terminal action: submit_results, ask_player, or reject_action.{flags_section}"""

    blocks: list[dict[str, Any]] = [{"type": "text", "text": base_prompt}]

    if rules_text:
        blocks.append({
            "type": "text",
            "text": f"\n## Game Rules\n{rules_text}",
            "cache_control": {"type": "ephemeral"},
        })

    # Build the final cached block: reminder_text + mandatory checklist
    # Checklist goes LAST for maximum model attention (§9)
    checklist = _build_enforcement_checklist(enforcement_config)
    final_block_parts = []
    if reminder_text:
        final_block_parts.append(f"## Critical Reminders\n{reminder_text}")
    final_block_parts.append(f"## MANDATORY CHECKLIST (verify before every terminal action)\n{checklist}")
    blocks.append({
        "type": "text",
        "text": "\n\n".join(final_block_parts),
        "cache_control": {"type": "ephemeral"},
    })

    return blocks


# ── Tool execution ────────────────────────────────────────────────────────────


async def _exec_roll_dice(
    db: AsyncSession,
    session: DMSession,
    message_id: uuid.UUID | None,
    tool_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    expression = tool_input["expression"]
    context = tool_input.get("context", "")

    try:
        result = parse_and_roll(expression)
    except ValueError as exc:
        return {"error": str(exc)}, False

    roll = DMRoll(
        session_id=session.id,
        dice_expression=result.expression,
        dice_faces=str(result.dice_faces),
        num_dice=result.num_dice,
        modifier=result.modifier,
        all_rolls=result.all_rolls,
        kept_rolls=result.kept_rolls,
        total=result.total,
        context=context,
    )
    db.add(roll)
    await db.flush()

    db.add(DMAction(
        session_id=session.id,
        message_id=message_id,
        action_type=DMActionType.roll,
        action_data={"roll_id": str(roll.id), "expression": expression, "total": result.total},
    ))
    await db.flush()

    return {
        "roll_id": str(roll.id),
        "expression": expression,
        "all_rolls": result.all_rolls,
        "kept_rolls": result.kept_rolls,
        "modifier": result.modifier,
        "total": result.total,
    }, False


async def _exec_set_stats(
    db: AsyncSession,
    session: DMSession,
    message_id: uuid.UUID | None,
    sheet: CharacterSheet,
    tool_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    character_id_str = tool_input.get("character_id", "")
    context = tool_input.get("context", "")
    absolute_values = tool_input.get("absolute_values") or {}
    deltas = {
        k: v for k, v in tool_input.items()
        if k not in ("character_id", "context", "absolute_values")
    }

    stat_schema: list[dict[str, Any]] = session.ruleset.stat_schema or []
    validation = apply_stat_deltas(
        stat_schema, sheet.stats or {}, deltas, absolute_values=absolute_values
    )

    sheet.stats = validation.new_stats

    # HP verification before permadeath (§5.2): DM cannot fabricate deaths at 30+ HP
    if validation.death_state and sheet.is_alive:
        enforcement = session.ruleset.enforcement_config or {}
        if enforcement.get("permadeath", True):
            # Double-check: verify at least one HP stat is actually at/below minimum
            if verify_death_legitimate(stat_schema, validation.new_stats):
                sheet.is_alive = False
            else:
                # Death state was triggered but HP never actually hit 0 — override
                validation.death_state = False
                logger.warning(
                    "dm_death_rejected_hp_positive",
                    session_id=str(session.id),
                    stats=validation.new_stats,
                )

    action_data: dict[str, Any] = {
        "character_id": character_id_str,
        "context": context,
        "changes": [
            {
                "name": c.name,
                "old": c.old_value,
                "new": c.new_value,
                "delta": c.delta,
                "absolute": c.was_absolute,
            }
            for c in validation.changes
        ],
        "death_state": validation.death_state,
    }
    if validation.unknown_stats:
        action_data["unknown_stats"] = validation.unknown_stats
    if validation.clamped_stats:
        action_data["clamped_stats"] = validation.clamped_stats

    db.add(DMAction(
        session_id=session.id,
        message_id=message_id,
        action_type=DMActionType.stat_update,
        action_data=action_data,
    ))
    await db.flush()

    return {
        "applied": [c.name for c in validation.changes],
        "changes": [
            {
                "name": c.name,
                "display_name": c.display_name,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "delta": c.delta,
            }
            for c in validation.changes
        ],
        "new_stats": validation.new_stats,
        "death_state": validation.death_state,
        "clamped": validation.clamped_stats,
        "unknown_ignored": validation.unknown_stats,
    }, False


async def _exec_set_inventory(
    db: AsyncSession,
    session: DMSession,
    message_id: uuid.UUID | None,
    sheet: CharacterSheet,
    tool_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    items = tool_input.get("items", [])
    context = tool_input.get("context", "")
    sheet.inventory = items
    db.add(DMAction(
        session_id=session.id,
        message_id=message_id,
        action_type=DMActionType.inventory_update,
        action_data={"context": context, "items": items},
    ))
    await db.flush()
    return {"inventory": items}, False


async def _exec_set_skills(
    db: AsyncSession,
    session: DMSession,
    message_id: uuid.UUID | None,
    sheet: CharacterSheet,
    tool_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    new_skills = tool_input.get("skills", {})
    context = tool_input.get("context", "")
    updated = {**sheet.skills, **new_skills}
    sheet.skills = updated
    db.add(DMAction(
        session_id=session.id,
        message_id=message_id,
        action_type=DMActionType.skill_update,
        action_data={"context": context, "skills": new_skills},
    ))
    await db.flush()
    return {"skills": updated}, False


async def _exec_set_spells(
    db: AsyncSession,
    session: DMSession,
    message_id: uuid.UUID | None,
    sheet: CharacterSheet,
    tool_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Handle add / remove / update spell actions (§3.5).

    Spells are stored as a list of dicts: [{name, circle, mp_cost, prepared, ...}].
    """
    action = tool_input.get("action", "add")
    incoming = tool_input.get("spells", [])
    context = tool_input.get("context", "")
    current: list[dict[str, Any]] = list(sheet.spells or [])

    if action == "add":
        existing_names = {s["name"] for s in current if isinstance(s, dict)}
        for spell in incoming:
            if isinstance(spell, dict) and spell.get("name") not in existing_names:
                current.append(spell)
                existing_names.add(spell["name"])
            elif isinstance(spell, dict):
                # Update existing (treat add as upsert)
                for i, s in enumerate(current):
                    if isinstance(s, dict) and s["name"] == spell["name"]:
                        current[i] = {**s, **spell}
                        break

    elif action == "remove":
        remove_names = {s["name"] for s in incoming if isinstance(s, dict)}
        current = [s for s in current if not (isinstance(s, dict) and s.get("name") in remove_names)]

    elif action == "update":
        for spell in incoming:
            if not isinstance(spell, dict) or not spell.get("name"):
                continue
            for i, s in enumerate(current):
                if isinstance(s, dict) and s.get("name") == spell["name"]:
                    current[i] = {**s, **spell}
                    break

    sheet.spells = current
    db.add(DMAction(
        session_id=session.id,
        message_id=message_id,
        action_type=DMActionType.spell_update,
        action_data={"context": context, "action": action, "spells": incoming},
    ))
    await db.flush()
    return {"spells": current, "action": action}, False


async def _exec_set_description(
    db: AsyncSession,
    session: DMSession,
    message_id: uuid.UUID | None,
    sheet: CharacterSheet,
    tool_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Update character description — anti-drift anchor (§3.4)."""
    description = tool_input.get("description", "")
    context = tool_input.get("context", "")
    sheet.description = description
    db.add(DMAction(
        session_id=session.id,
        message_id=message_id,
        action_type=DMActionType.description_update,
        action_data={"context": context, "description": description},
    ))
    await db.flush()
    return {"description": description}, False


# ── Main evaluation loop ──────────────────────────────────────────────────────


async def evaluate_action(
    db: AsyncSession,
    session: DMSession,
    sheet: CharacterSheet,
    recent_messages: list[dict[str, str]],
    player_action: str,
    message_id: uuid.UUID | None = None,
    pre_validation: PreValidationResult | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Async generator driving the DM agent evaluation.

    Yields SSE-ready event dicts as the DM makes tool calls.
    The final yielded event is always type "dm_done" with a DMEvalResult payload.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise AppError(
            "Anthropic API key not configured",
            status_code=503,
            error_code="dm_provider_not_configured",
        )

    ruleset = session.ruleset
    model = ruleset.recommended_model or settings.DM_DEFAULT_MODEL
    context_window = ruleset.context_window or 10
    max_tokens = ruleset.dm_max_tokens or 2000
    enforcement = ruleset.enforcement_config or {}
    tools = build_dm_tools(ruleset.stat_schema or [])

    pre_flags = pre_validation.flags_for_prompt() if pre_validation else ""
    system_blocks = _build_system_prompt_blocks(
        ruleset.name,
        ruleset.stat_schema or [],
        sheet,
        ruleset.rules_text,
        ruleset.reminder_text,
        enforcement,
        pre_flags,
    )

    context_messages = recent_messages[-context_window:]
    messages: list[dict[str, Any]] = [
        *[{"role": m["role"], "content": m["content"]} for m in context_messages],
        {"role": "user", "content": player_action},
    ]

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    yield {"type": "dm_thinking", "data": {}}

    roll_ids: list[uuid.UUID] = []
    stat_changes: list[StatChangeSummary] = []
    spell_changes: list[dict[str, Any]] = []
    death_state = False
    verdict = DMVerdict.continue_
    summary: str | None = None
    narrative_hint: str | None = None
    rejection_reason: str | None = None
    ask_question: str | None = None
    total_prompt_tokens = 0
    total_completion_tokens = 0
    start_time = time.monotonic()
    game_event_id: uuid.UUID | None = None

    for _iteration in range(_MAX_TOOL_ITERATIONS):
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=messages,
            tools=tools,  # type: ignore[arg-type]
        )

        if hasattr(response, "usage") and response.usage:
            total_prompt_tokens += getattr(response.usage, "input_tokens", 0)
            total_completion_tokens += getattr(response.usage, "output_tokens", 0)

        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            logger.warning("dm_agent_no_terminal_tool", session_id=str(session.id))
            break

        tool_results: list[dict[str, Any]] = []
        terminal = False

        for block in tool_uses:
            tool_name: str = block.name
            tool_input: dict[str, Any] = block.input  # type: ignore[assignment]
            result_data: dict[str, Any] = {}

            if tool_name == "roll_dice":
                result_data, _ = await _exec_roll_dice(db, session, message_id, tool_input)
                if "roll_id" in result_data:
                    roll_ids.append(uuid.UUID(result_data["roll_id"]))
                yield {"type": "dm_roll", "data": result_data}

            elif tool_name == "set_stats":
                result_data, _ = await _exec_set_stats(db, session, message_id, sheet, tool_input)
                if result_data.get("applied"):
                    for change in result_data.get("changes", []):
                        stat_changes.append(StatChangeSummary(
                            name=change["name"],
                            display_name=change.get("display_name", change["name"]),
                            old_value=change["old_value"],
                            new_value=change["new_value"],
                            delta=change.get("delta"),
                        ))
                if result_data.get("death_state"):
                    death_state = True
                yield {
                    "type": "dm_stat_update",
                    "data": {
                        "character_id": tool_input.get("character_id"),
                        "new_stats": result_data.get("new_stats", {}),
                        "changes": result_data.get("changes", []),
                        "death_state": result_data.get("death_state", False),
                        "is_alive": sheet.is_alive,
                    },
                }

            elif tool_name == "set_inventory":
                result_data, _ = await _exec_set_inventory(db, session, message_id, sheet, tool_input)
                yield {"type": "dm_inventory_update", "data": result_data}

            elif tool_name == "set_skills":
                result_data, _ = await _exec_set_skills(db, session, message_id, sheet, tool_input)
                yield {"type": "dm_skill_update", "data": result_data}

            elif tool_name == "set_spells":
                result_data, _ = await _exec_set_spells(db, session, message_id, sheet, tool_input)
                if "spells" in result_data:
                    spell_changes.append({
                        "action": tool_input.get("action"),
                        "spells": tool_input.get("spells", []),
                    })
                yield {"type": "dm_spell_update", "data": result_data}

            elif tool_name == "set_description":
                result_data, _ = await _exec_set_description(db, session, message_id, sheet, tool_input)
                yield {"type": "dm_description_update", "data": result_data}

            elif tool_name == "submit_results":
                summary = tool_input.get("summary")
                narrative_hint = tool_input.get("narrative_hint")
                roll_ids_raw = tool_input.get("roll_ids", [])
                for rid in roll_ids_raw:
                    try:
                        roll_ids.append(uuid.UUID(str(rid)))
                    except ValueError:
                        pass
                db.add(DMAction(
                    session_id=session.id,
                    message_id=message_id,
                    action_type=DMActionType.submit,
                    action_data={"summary": summary, "narrative_hint": narrative_hint},
                ))
                await db.flush()
                verdict = DMVerdict.continue_
                terminal = True

            elif tool_name == "ask_player":
                ask_question = tool_input.get("question")
                db.add(DMAction(
                    session_id=session.id,
                    message_id=message_id,
                    action_type=DMActionType.ask_player,
                    action_data={"question": ask_question},
                ))
                await db.flush()
                verdict = DMVerdict.ask
                yield {"type": "dm_ask", "data": {"question": ask_question}}
                terminal = True

            elif tool_name == "reject_action":
                rejection_reason = tool_input.get("reason")
                db.add(DMAction(
                    session_id=session.id,
                    message_id=message_id,
                    action_type=DMActionType.reject,
                    action_data={"reason": rejection_reason},
                ))
                await db.flush()
                verdict = DMVerdict.reject
                yield {"type": "dm_reject", "data": {"reason": rejection_reason}}
                terminal = True

            else:
                logger.warning("dm_unknown_tool", tool_name=tool_name)
                result_data = {"error": f"Unknown tool: {tool_name}"}

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(
                    result_data if tool_name not in ("ask_player", "reject_action") else {"ok": True}
                ),
            })

            if terminal:
                break

        messages.append({"role": "user", "content": tool_results})

        if terminal:
            latency_ms = int((time.monotonic() - start_time) * 1000)
            ruling_str = (
                "reject" if verdict == DMVerdict.reject
                else "ask" if verdict == DMVerdict.ask
                else ("roll" if roll_ids else "pass")
            )

            game_event = GameEvent(
                session_id=session.id,
                message_id=message_id,
                player_action=player_action,
                ruling=ruling_str,
                ruling_reason=rejection_reason or ask_question,
                stat_changes=[
                    {
                        "name": sc.name,
                        "display_name": sc.display_name,
                        "old_value": sc.old_value,
                        "new_value": sc.new_value,
                        "delta": sc.delta,
                    }
                    for sc in stat_changes
                ],
                dm_model_used=model,
                dm_prompt_tokens=total_prompt_tokens,
                dm_completion_tokens=total_completion_tokens,
                dm_latency_ms=latency_ms,
                pre_validation=pre_validation.to_dict() if pre_validation else None,
                question_text=ask_question,
                summary=summary,
            )
            db.add(game_event)
            await db.flush()
            game_event_id = game_event.id
            await db.commit()
            break

        await db.commit()

    eval_result = DMEvalResult(
        verdict=verdict,
        summary=summary,
        narrative_hint=narrative_hint,
        rejection_reason=rejection_reason,
        ask_question=ask_question,
        stat_changes=stat_changes,
        roll_ids=roll_ids,
        death_state=death_state,
        game_event_id=game_event_id,
    )
    # Build narrative context including character description for anti-drift (§3.4)
    eval_result.narrative_context = build_narrative_context(
        eval_result, character_description=sheet.description
    )

    yield {"type": "dm_done", "data": eval_result.model_dump(mode="json")}


# ── DB helpers ────────────────────────────────────────────────────────────────


async def get_session_by_chat(
    db: AsyncSession, chat_id: uuid.UUID
) -> DMSession | None:
    result = await db.execute(
        select(DMSession)
        .options(selectinload(DMSession.ruleset))
        .where(DMSession.chat_id == chat_id, DMSession.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_or_create_sheet(
    db: AsyncSession,
    session: DMSession,
    character_id: uuid.UUID | None,
    display_name: str | None = None,
) -> CharacterSheet:
    """Return existing character sheet or create one initialized from the ruleset schema."""
    query = select(CharacterSheet).where(CharacterSheet.session_id == session.id)
    if character_id:
        query = query.where(CharacterSheet.character_id == character_id)
    else:
        query = query.where(CharacterSheet.is_player.is_(True))

    result = await db.execute(query)
    sheet = result.scalar_one_or_none()

    if sheet is None:
        initial_stats = initialize_stats(session.ruleset.stat_schema or [])
        sheet = CharacterSheet(
            session_id=session.id,
            character_id=character_id,
            is_player=True,
            is_alive=True,
            display_name=display_name,
            stats=initial_stats,
            inventory=[],
            skills={},
            spells=[],
        )
        db.add(sheet)
        await db.flush()

    return sheet
