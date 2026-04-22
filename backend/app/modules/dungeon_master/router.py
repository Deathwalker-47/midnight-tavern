"""Dungeon Master API endpoints.

All endpoints currently unprotected — auth dependency injection will be
added when the auth module is complete (Sprint A task 3).

SSE streaming endpoint follows the same pattern that will be used for
the main chat streaming endpoint.
"""

import json
import uuid
from collections.abc import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.db.session import get_db
from app.modules.dungeon_master.models import (
    CharacterSheet,
    DMRoll,
    DMSession,
    GameRuleset,
)
from app.modules.dungeon_master.schemas import (
    CharacterSheetResponse,
    CreateRulesetRequest,
    CreateSessionRequest,
    CreateSheetRequest,
    EvaluateRequest,
    RollListResponse,
    RollRequest,
    RollResponse,
    RulesetResponse,
    SessionResponse,
    UpdateRulesetRequest,
    UpdateSheetRequest,
)
from app.modules.dungeon_master.service import (
    evaluate_action,
    get_or_create_sheet,
    get_session_by_chat,
)
from app.modules.dungeon_master.dice import parse_and_roll
from app.modules.dungeon_master.models import DMRoll

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/dm", tags=["dungeon-master"])


# ── Game Rulesets ─────────────────────────────────────────────────────────────


@router.post("/rulesets", response_model=RulesetResponse, status_code=status.HTTP_201_CREATED)
async def create_ruleset(
    body: CreateRulesetRequest,
    db: AsyncSession = Depends(get_db),
) -> RulesetResponse:
    ruleset = GameRuleset(
        name=body.name,
        description=body.description,
        stat_schema=[s.model_dump() for s in body.stat_schema],
        rules_text=body.rules_text,
        recommended_model=body.recommended_model,
        is_public=body.is_public,
    )
    db.add(ruleset)
    await db.commit()
    await db.refresh(ruleset)
    return RulesetResponse.model_validate(ruleset)


@router.get("/rulesets", response_model=list[RulesetResponse])
async def list_rulesets(
    db: AsyncSession = Depends(get_db),
) -> list[RulesetResponse]:
    result = await db.execute(
        select(GameRuleset).where(GameRuleset.deleted_at.is_(None)).order_by(GameRuleset.created_at.desc())
    )
    rulesets = result.scalars().all()
    return [RulesetResponse.model_validate(r) for r in rulesets]


@router.get("/rulesets/{ruleset_id}", response_model=RulesetResponse)
async def get_ruleset(
    ruleset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RulesetResponse:
    result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == ruleset_id,
            GameRuleset.deleted_at.is_(None),
        )
    )
    ruleset = result.scalar_one_or_none()
    if ruleset is None:
        raise AppError("Ruleset not found", status_code=404, error_code="ruleset_not_found")
    return RulesetResponse.model_validate(ruleset)


@router.put("/rulesets/{ruleset_id}", response_model=RulesetResponse)
async def update_ruleset(
    ruleset_id: uuid.UUID,
    body: UpdateRulesetRequest,
    db: AsyncSession = Depends(get_db),
) -> RulesetResponse:
    result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == ruleset_id,
            GameRuleset.deleted_at.is_(None),
        )
    )
    ruleset = result.scalar_one_or_none()
    if ruleset is None:
        raise AppError("Ruleset not found", status_code=404, error_code="ruleset_not_found")

    if body.name is not None:
        ruleset.name = body.name
    if body.description is not None:
        ruleset.description = body.description
    if body.stat_schema is not None:
        ruleset.stat_schema = [s.model_dump() for s in body.stat_schema]
    if body.rules_text is not None:
        ruleset.rules_text = body.rules_text
    if body.recommended_model is not None:
        ruleset.recommended_model = body.recommended_model
    if body.is_public is not None:
        ruleset.is_public = body.is_public

    await db.commit()
    await db.refresh(ruleset)
    return RulesetResponse.model_validate(ruleset)


@router.delete("/rulesets/{ruleset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ruleset(
    ruleset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    from datetime import datetime, timezone

    result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == ruleset_id,
            GameRuleset.deleted_at.is_(None),
        )
    )
    ruleset = result.scalar_one_or_none()
    if ruleset is None:
        raise AppError("Ruleset not found", status_code=404, error_code="ruleset_not_found")

    ruleset.deleted_at = datetime.now(timezone.utc)
    await db.commit()


# ── DM Sessions ───────────────────────────────────────────────────────────────


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    # Verify ruleset exists
    ruleset_result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == body.ruleset_id,
            GameRuleset.deleted_at.is_(None),
        )
    )
    if ruleset_result.scalar_one_or_none() is None:
        raise AppError("Ruleset not found", status_code=404, error_code="ruleset_not_found")

    # Deactivate any existing session for this chat
    existing = await get_session_by_chat(db, body.chat_id)
    if existing:
        from datetime import datetime, timezone
        existing.deleted_at = datetime.now(timezone.utc)

    session = DMSession(chat_id=body.chat_id, ruleset_id=body.ruleset_id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionResponse.model_validate(session)


@router.get("/sessions/{chat_id}", response_model=SessionResponse)
async def get_session(
    chat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    session = await get_session_by_chat(db, chat_id)
    if session is None:
        raise AppError("No active DM session for this chat", status_code=404, error_code="session_not_found")
    return SessionResponse.model_validate(session)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    from datetime import datetime, timezone

    result = await db.execute(
        select(DMSession).where(
            DMSession.id == session_id,
            DMSession.deleted_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise AppError("Session not found", status_code=404, error_code="session_not_found")

    session.deleted_at = datetime.now(timezone.utc)
    await db.commit()


# ── Character Sheets ──────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/sheet", response_model=list[CharacterSheetResponse])
async def list_sheets(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[CharacterSheetResponse]:
    result = await db.execute(
        select(CharacterSheet).where(CharacterSheet.session_id == session_id)
    )
    sheets = result.scalars().all()
    return [CharacterSheetResponse.model_validate(s) for s in sheets]


@router.post("/sessions/{session_id}/sheet", response_model=CharacterSheetResponse, status_code=status.HTTP_201_CREATED)
async def create_sheet(
    session_id: uuid.UUID,
    body: CreateSheetRequest,
    db: AsyncSession = Depends(get_db),
) -> CharacterSheetResponse:
    result = await db.execute(
        select(DMSession)
        .options(selectinload(DMSession.ruleset))
        .where(DMSession.id == session_id, DMSession.deleted_at.is_(None))
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise AppError("Session not found", status_code=404, error_code="session_not_found")

    sheet = CharacterSheet(
        session_id=session_id,
        character_id=body.character_id,
        is_player=body.is_player,
        display_name=body.display_name,
        stats=body.stats,
        inventory=body.inventory,
        skills=body.skills,
    )
    db.add(sheet)
    await db.commit()
    await db.refresh(sheet)
    return CharacterSheetResponse.model_validate(sheet)


@router.patch("/sessions/{session_id}/sheet/{sheet_id}", response_model=CharacterSheetResponse)
async def update_sheet(
    session_id: uuid.UUID,
    sheet_id: uuid.UUID,
    body: UpdateSheetRequest,
    db: AsyncSession = Depends(get_db),
) -> CharacterSheetResponse:
    result = await db.execute(
        select(CharacterSheet).where(
            CharacterSheet.id == sheet_id,
            CharacterSheet.session_id == session_id,
        )
    )
    sheet = result.scalar_one_or_none()
    if sheet is None:
        raise AppError("Character sheet not found", status_code=404, error_code="sheet_not_found")

    if body.stats is not None:
        sheet.stats = body.stats
    if body.inventory is not None:
        sheet.inventory = body.inventory
    if body.skills is not None:
        sheet.skills = body.skills
    if body.display_name is not None:
        sheet.display_name = body.display_name

    await db.commit()
    await db.refresh(sheet)
    return CharacterSheetResponse.model_validate(sheet)


# ── Dice Rolls ────────────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/roll", response_model=RollResponse, status_code=status.HTTP_201_CREATED)
async def manual_roll(
    session_id: uuid.UUID,
    body: RollRequest,
    db: AsyncSession = Depends(get_db),
) -> RollResponse:
    """Manual dice roll triggered by the player (not the DM agent)."""
    session_result = await db.execute(
        select(DMSession).where(
            DMSession.id == session_id,
            DMSession.deleted_at.is_(None),
        )
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise AppError("Session not found", status_code=404, error_code="session_not_found")

    try:
        result = parse_and_roll(body.expression)
    except ValueError as exc:
        raise AppError(str(exc), status_code=422, error_code="invalid_dice_expression") from exc

    roll = DMRoll(
        session_id=session_id,
        dice_expression=result.expression,
        dice_faces=str(result.dice_faces),
        num_dice=result.num_dice,
        modifier=result.modifier,
        all_rolls=result.all_rolls,
        kept_rolls=result.kept_rolls,
        total=result.total,
        context=body.context,
    )
    db.add(roll)
    await db.commit()
    await db.refresh(roll)
    return RollResponse.model_validate(roll)


@router.get("/sessions/{session_id}/rolls", response_model=RollListResponse)
async def list_rolls(
    session_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> RollListResponse:
    from sqlalchemy import func

    count_result = await db.execute(
        select(func.count()).select_from(DMRoll).where(DMRoll.session_id == session_id)
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(DMRoll)
        .where(DMRoll.session_id == session_id)
        .order_by(DMRoll.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rolls = result.scalars().all()
    return RollListResponse(
        rolls=[RollResponse.model_validate(r) for r in rolls],
        total=total,
    )


# ── DM Evaluation (SSE) ───────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/evaluate")
async def evaluate(
    session_id: uuid.UUID,
    body: EvaluateRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Trigger the DM agent to evaluate a player action.

    Returns a Server-Sent Events stream. Event types:
      dm_thinking       — agent started
      dm_roll           — dice rolled: {expression, all_rolls, kept_rolls, total, roll_id}
      dm_stat_update    — stats changed: {character_id, new_stats, death_state}
      dm_inventory_update — inventory changed: {inventory}
      dm_skill_update   — skills changed: {skills}
      dm_reject         — action rejected: {reason}
      dm_ask            — clarification needed: {question}
      dm_done           — evaluation complete: DMEvalResult payload
    """
    result = await db.execute(
        select(DMSession)
        .options(selectinload(DMSession.ruleset))
        .where(DMSession.id == session_id, DMSession.deleted_at.is_(None))
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise AppError("Session not found", status_code=404, error_code="session_not_found")

    sheet = await get_or_create_sheet(db, session, body.character_id)

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for event in evaluate_action(
                db=db,
                session=session,
                sheet=sheet,
                recent_messages=body.recent_messages,
                player_action=body.player_action,
            ):
                event_type = event["type"]
                event_data = json.dumps(event.get("data", {}))
                yield f"event: {event_type}\ndata: {event_data}\n\n"
        except AppError as exc:
            yield f"event: dm_error\ndata: {json.dumps({'code': exc.error_code, 'message': exc.message})}\n\n"
        except Exception as exc:
            logger.error("dm_evaluate_error", exc_info=exc, session_id=str(session_id))
            yield f"event: dm_error\ndata: {json.dumps({'code': 'internal_error', 'message': 'DM evaluation failed'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
