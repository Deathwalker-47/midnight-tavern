"""DM agent service — orchestrates the agentic tool-calling evaluation loop.

Flow per player action:
  1. Load session + character sheet + ruleset from DB
  2. Generate DM tools dynamically from stat schema
  3. Build stripped DM system prompt (last 10 msgs + char sheet + rules)
  4. Call DM model (cheap/fast) with tools via Anthropic API
  5. Process tool calls in an agentic loop until terminal action
  6. Store rolls + actions in DB atomically
  7. Yield SSE events as each tool call executes
  8. Return final DMEvalResult as the last event

The system prompt uses prompt caching on the rules_text block (which
changes infrequently) to reduce latency and cost on repeated evaluations.
"""

import json
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
)
from app.modules.dungeon_master.schemas import DMEvalResult, DMVerdict, StatChangeSummary
from app.modules.dungeon_master.tools import build_dm_tools
from app.modules.dungeon_master.validation import apply_stat_deltas, initialize_stats

logger = structlog.get_logger(__name__)

_MAX_CONTEXT_MESSAGES = 10
_MAX_TOOL_ITERATIONS = 15


# ── Prompt construction ───────────────────────────────────────────────────────


def _build_system_prompt_blocks(
    ruleset_name: str,
    stat_schema: list[dict[str, Any]],
    sheet: CharacterSheet,
    rules_text: str | None,
) -> list[dict[str, Any]]:
    """Build system prompt as content blocks with prompt caching on rules."""
    stat_lines = "\n".join(
        f"  {name}: {value}" for name, value in (sheet.stats or {}).items()
    )
    inventory_str = (
        ", ".join(str(i) for i in sheet.inventory) if sheet.inventory else "Empty"
    )
    skills_str = (
        ", ".join(f"{k}:{v}" for k, v in (sheet.skills or {}).items()) or "None"
    )

    base_prompt = f"""\
You are the Dungeon Master for a roleplay session using the "{ruleset_name}" ruleset.

Your role: evaluate the player's latest action for mechanical implications, roll dice when required, update stats/inventory/skills, and enforce game rules. The story AI handles all narrative — you handle ONLY game mechanics.

## Current Character Sheet
Character: {sheet.display_name or "Player"}
Stats:
{stat_lines or "  (none defined)"}
Inventory: {inventory_str}
Skills: {skills_str}

## Instructions
1. Determine whether the player's action requires a mechanical resolution.
2. If no mechanics apply, call submit_results immediately with an empty narrative_hint.
3. If mechanics apply: roll appropriate dice, apply stat changes, then call submit_results.
4. For number stats, always use DELTA values (negative = decrease, positive = increase, 0 = reset to initial).
5. Only update stats that actually changed.
6. Always end with exactly one terminal action: submit_results, ask_player, or reject_action."""

    blocks: list[dict[str, Any]] = [{"type": "text", "text": base_prompt}]

    if rules_text:
        # Cache the rules block — it's the largest and changes infrequently.
        # Appended last to exploit LLM recency bias for rule enforcement.
        blocks.append({
            "type": "text",
            "text": f"\n## Game Rules\n{rules_text}",
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
    deltas = {k: v for k, v in tool_input.items() if k not in ("character_id", "context")}

    stat_schema: list[dict[str, Any]] = session.ruleset.stat_schema or []
    validation = apply_stat_deltas(stat_schema, sheet.stats or {}, deltas)

    sheet.stats = validation.new_stats

    action_data: dict[str, Any] = {
        "character_id": character_id_str,
        "context": context,
        "changes": [
            {"name": c.name, "old": c.old_value, "new": c.new_value, "delta": c.delta}
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


# ── Main evaluation loop ──────────────────────────────────────────────────────


async def evaluate_action(
    db: AsyncSession,
    session: DMSession,
    sheet: CharacterSheet,
    recent_messages: list[dict[str, str]],
    player_action: str,
    message_id: uuid.UUID | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Async generator that drives the DM agent evaluation.

    Yields SSE-ready event dicts as the DM makes tool calls.
    The final yielded event is always type "dm_complete" with a DMEvalResult payload.

    Usage:
        async for event in evaluate_action(...):
            # event = {"type": "dm_roll", "data": {...}}
    """
    if not settings.ANTHROPIC_API_KEY:
        raise AppError(
            "Anthropic API key not configured",
            status_code=503,
            error_code="dm_provider_not_configured",
        )

    ruleset = session.ruleset
    model = ruleset.recommended_model or settings.DM_DEFAULT_MODEL
    tools = build_dm_tools(ruleset.stat_schema or [])
    system_blocks = _build_system_prompt_blocks(
        ruleset.name, ruleset.stat_schema or [], sheet, ruleset.rules_text
    )

    # Trim to last N messages and append the player's current action
    context_messages = recent_messages[-_MAX_CONTEXT_MESSAGES:]
    messages: list[dict[str, Any]] = [
        *[{"role": m["role"], "content": m["content"]} for m in context_messages],
        {"role": "user", "content": player_action},
    ]

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    yield {"type": "dm_thinking", "data": {}}

    roll_ids: list[uuid.UUID] = []
    stat_changes: list[StatChangeSummary] = []
    death_state = False
    verdict = DMVerdict.continue_
    summary: str | None = None
    narrative_hint: str | None = None
    rejection_reason: str | None = None
    ask_question: str | None = None

    for _iteration in range(_MAX_TOOL_ITERATIONS):
        response = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_blocks,
            messages=messages,
            tools=tools,  # type: ignore[arg-type]
        )

        # Append assistant response to message history
        messages.append({"role": "assistant", "content": response.content})

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            # Model finished without a terminal tool — treat as implicit submit
            logger.warning("dm_agent_no_terminal_tool", session_id=str(session.id))
            break

        tool_results: list[dict[str, Any]] = []
        terminal = False

        for block in tool_uses:
            tool_name: str = block.name
            tool_input: dict[str, Any] = block.input  # type: ignore[assignment]

            if tool_name == "roll_dice":
                result_data, _ = await _exec_roll_dice(db, session, message_id, tool_input)
                if "roll_id" in result_data:
                    roll_ids.append(uuid.UUID(result_data["roll_id"]))
                yield {"type": "dm_roll", "data": result_data}

            elif tool_name == "set_stats":
                result_data, _ = await _exec_set_stats(db, session, message_id, sheet, tool_input)
                if result_data.get("applied"):
                    stat_schema = ruleset.stat_schema or []
                    schema_map = {s["name"]: s for s in stat_schema}
                    for change_name in result_data["applied"]:
                        old_v = (sheet.stats or {}).get(change_name)
                        new_v = result_data["new_stats"].get(change_name)
                        stat_changes.append(StatChangeSummary(
                            name=change_name,
                            display_name=schema_map.get(change_name, {}).get("display_name", change_name),
                            old_value=old_v,
                            new_value=new_v,
                            delta=tool_input.get(change_name),
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
                    },
                }

            elif tool_name == "set_inventory":
                result_data, _ = await _exec_set_inventory(db, session, message_id, sheet, tool_input)
                yield {"type": "dm_inventory_update", "data": result_data}

            elif tool_name == "set_skills":
                result_data, _ = await _exec_set_skills(db, session, message_id, sheet, tool_input)
                yield {"type": "dm_skill_update", "data": result_data}

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
                "content": json.dumps(result_data if tool_name not in ("ask_player", "reject_action") else {"ok": True}),
            })

            if terminal:
                break

        messages.append({"role": "user", "content": tool_results})
        await db.commit()

        if terminal:
            break

    eval_result = DMEvalResult(
        verdict=verdict,
        summary=summary,
        narrative_hint=narrative_hint,
        rejection_reason=rejection_reason,
        ask_question=ask_question,
        stat_changes=stat_changes,
        roll_ids=roll_ids,
        death_state=death_state,
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
            display_name=display_name,
            stats=initial_stats,
            inventory=[],
            skills={},
        )
        db.add(sheet)
        await db.flush()

    return sheet
