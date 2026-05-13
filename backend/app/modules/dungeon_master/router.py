"""Dungeon Master API endpoints."""

import json
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.auth.service import get_current_user
from app.modules.chats.service import get_chat
from app.modules.dungeon_master.dice import parse_and_roll
from app.modules.dungeon_master.models import (
    CharacterSheet,
    DMConfigAttachment,
    DMRoll,
    DMSession,
    GameEvent,
    GameRuleset,
)
from app.modules.dungeon_master.schemas import (
    AnswerQuestionRequest,
    AttachmentResponse,
    CharacterSheetResponse,
    CreateAttachmentRequest,
    CreateRulesetRequest,
    CreateSessionRequest,
    CreateSheetRequest,
    EvaluateRequest,
    GameEventListResponse,
    GameEventResponse,
    RollListResponse,
    RollRequest,
    RollResponse,
    RollStatsResponse,
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

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/dm", tags=["dungeon-master"])


# ── Ownership helper ──────────────────────────────────────────────────────────


async def _get_session_owned(
    db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> DMSession:
    """Fetch a DM session and verify its chat belongs to the user."""
    result = await db.execute(
        select(DMSession)
        .options(selectinload(DMSession.ruleset))
        .where(DMSession.id == session_id, DMSession.deleted_at.is_(None))
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise AppError("Session not found", status_code=404, error_code="session_not_found")
    # Validate chat ownership (raises 404 if not owned)
    await get_chat(db, session.chat_id, user_id)
    return session


# ── Game Rulesets ─────────────────────────────────────────────────────────────


@router.post("/rulesets", response_model=RulesetResponse, status_code=status.HTTP_201_CREATED)
async def create_ruleset(
    body: CreateRulesetRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RulesetResponse:
    ruleset = GameRuleset(
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        stat_schema=[s.model_dump() for s in body.stat_schema],
        rules_text=body.rules_text,
        reminder_text=body.reminder_text,
        instruction=body.instruction,
        player_guide=body.player_guide,
        recommended_model=body.recommended_model,
        recommended_provider=body.recommended_provider,
        dm_temperature=body.dm_temperature,
        dm_max_tokens=body.dm_max_tokens,
        context_window=body.context_window,
        enforcement_config=body.enforcement_config.model_dump(),
        is_public=body.is_public,
    )
    db.add(ruleset)
    await db.commit()
    await db.refresh(ruleset)
    return RulesetResponse.model_validate(ruleset)


@router.get("/rulesets", response_model=list[RulesetResponse])
async def list_rulesets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RulesetResponse]:
    """List rulesets owned by the user plus public ones."""
    result = await db.execute(
        select(GameRuleset)
        .where(
            GameRuleset.deleted_at.is_(None),
            or_(GameRuleset.user_id == current_user.id, GameRuleset.is_public.is_(True)),
        )
        .order_by(GameRuleset.created_at.desc())
    )
    return [RulesetResponse.model_validate(r) for r in result.scalars().all()]


@router.get("/rulesets/public", response_model=list[RulesetResponse])
async def list_public_rulesets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[RulesetResponse]:
    result = await db.execute(
        select(GameRuleset)
        .where(GameRuleset.deleted_at.is_(None), GameRuleset.is_public.is_(True))
        .order_by(GameRuleset.created_at.desc())
    )
    return [RulesetResponse.model_validate(r) for r in result.scalars().all()]


@router.get("/rulesets/{ruleset_id}", response_model=RulesetResponse)
async def get_ruleset(
    ruleset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RulesetResponse:
    result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == ruleset_id,
            GameRuleset.deleted_at.is_(None),
            or_(GameRuleset.user_id == current_user.id, GameRuleset.is_public.is_(True)),
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RulesetResponse:
    result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == ruleset_id,
            GameRuleset.user_id == current_user.id,
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
    if body.reminder_text is not None:
        ruleset.reminder_text = body.reminder_text
    if body.instruction is not None:
        ruleset.instruction = body.instruction
    if body.player_guide is not None:
        ruleset.player_guide = body.player_guide
    if body.recommended_model is not None:
        ruleset.recommended_model = body.recommended_model
    if body.recommended_provider is not None:
        ruleset.recommended_provider = body.recommended_provider
    if body.dm_temperature is not None:
        ruleset.dm_temperature = body.dm_temperature
    if body.dm_max_tokens is not None:
        ruleset.dm_max_tokens = body.dm_max_tokens
    if body.context_window is not None:
        ruleset.context_window = body.context_window
    if body.enforcement_config is not None:
        ruleset.enforcement_config = body.enforcement_config.model_dump()
    if body.is_public is not None:
        ruleset.is_public = body.is_public

    await db.commit()
    await db.refresh(ruleset)
    return RulesetResponse.model_validate(ruleset)


@router.delete("/rulesets/{ruleset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ruleset(
    ruleset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == ruleset_id,
            GameRuleset.user_id == current_user.id,
            GameRuleset.deleted_at.is_(None),
        )
    )
    ruleset = result.scalar_one_or_none()
    if ruleset is None:
        raise AppError("Ruleset not found", status_code=404, error_code="ruleset_not_found")

    ruleset.deleted_at = datetime.now(timezone.utc)
    await db.commit()


@router.post("/rulesets/{ruleset_id}/duplicate", response_model=RulesetResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_ruleset(
    ruleset_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RulesetResponse:
    result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == ruleset_id,
            GameRuleset.deleted_at.is_(None),
            or_(GameRuleset.user_id == current_user.id, GameRuleset.is_public.is_(True)),
        )
    )
    source = result.scalar_one_or_none()
    if source is None:
        raise AppError("Ruleset not found", status_code=404, error_code="ruleset_not_found")

    clone = GameRuleset(
        name=f"{source.name} (copy)",
        description=source.description,
        stat_schema=list(source.stat_schema),
        rules_text=source.rules_text,
        reminder_text=source.reminder_text,
        instruction=source.instruction,
        player_guide=source.player_guide,
        recommended_model=source.recommended_model,
        recommended_provider=source.recommended_provider,
        dm_temperature=source.dm_temperature,
        dm_max_tokens=source.dm_max_tokens,
        context_window=source.context_window,
        enforcement_config=dict(source.enforcement_config),
        is_public=False,
        user_id=current_user.id,
    )
    db.add(clone)
    await db.commit()
    await db.refresh(clone)
    return RulesetResponse.model_validate(clone)


# ── DM Sessions ───────────────────────────────────────────────────────────────


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    # Verify the chat belongs to the user
    await get_chat(db, body.chat_id, current_user.id)

    ruleset_result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == body.ruleset_id,
            GameRuleset.deleted_at.is_(None),
            or_(GameRuleset.user_id == current_user.id, GameRuleset.is_public.is_(True)),
        )
    )
    if ruleset_result.scalar_one_or_none() is None:
        raise AppError("Ruleset not found", status_code=404, error_code="ruleset_not_found")

    existing = await get_session_by_chat(db, body.chat_id)
    if existing:
        existing.deleted_at = datetime.now(timezone.utc)

    session = DMSession(chat_id=body.chat_id, ruleset_id=body.ruleset_id)
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return SessionResponse.model_validate(session)


@router.get("/sessions/{chat_id}", response_model=SessionResponse)
async def get_session(
    chat_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    await get_chat(db, chat_id, current_user.id)
    session = await get_session_by_chat(db, chat_id)
    if session is None:
        raise AppError("No active DM session for this chat", status_code=404, error_code="session_not_found")
    return SessionResponse.model_validate(session)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    session = await _get_session_owned(db, session_id, current_user.id)
    session.deleted_at = datetime.now(timezone.utc)
    await db.commit()


# ── Character Sheets ──────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/sheet", response_model=list[CharacterSheetResponse])
async def list_sheets(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CharacterSheetResponse]:
    await _get_session_owned(db, session_id, current_user.id)
    result = await db.execute(
        select(CharacterSheet).where(CharacterSheet.session_id == session_id)
    )
    return [CharacterSheetResponse.model_validate(s) for s in result.scalars().all()]


@router.post("/sessions/{session_id}/sheet", response_model=CharacterSheetResponse, status_code=status.HTTP_201_CREATED)
async def create_sheet(
    session_id: uuid.UUID,
    body: CreateSheetRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CharacterSheetResponse:
    await _get_session_owned(db, session_id, current_user.id)

    sheet = CharacterSheet(
        session_id=session_id,
        character_id=body.character_id,
        is_player=body.is_player,
        is_alive=True,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CharacterSheetResponse:
    await _get_session_owned(db, session_id, current_user.id)
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
    if body.is_alive is not None:
        sheet.is_alive = body.is_alive

    await db.commit()
    await db.refresh(sheet)
    return CharacterSheetResponse.model_validate(sheet)


# ── Dice Rolls ────────────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/roll", response_model=RollResponse, status_code=status.HTTP_201_CREATED)
async def manual_roll(
    session_id: uuid.UUID,
    body: RollRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RollResponse:
    await _get_session_owned(db, session_id, current_user.id)

    try:
        result = parse_and_roll(body.expression)
    except ValueError as exc:
        raise AppError(str(exc), status_code=422, error_code="invalid_dice_expression") from exc

    success: bool | None = None
    if body.dc is not None:
        success = (result.total + (body.dc or 0)) >= body.dc if body.dc else None
        success = result.total >= body.dc

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
        dc=body.dc,
        success=success,
        advantage=body.advantage,
        disadvantage=body.disadvantage,
        stat_used=body.stat_used,
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RollListResponse:
    await _get_session_owned(db, session_id, current_user.id)
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
    return RollListResponse(
        rolls=[RollResponse.model_validate(r) for r in result.scalars().all()],
        total=total,
    )


@router.get("/sessions/{session_id}/rolls/stats", response_model=RollStatsResponse)
async def roll_stats(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RollStatsResponse:
    await _get_session_owned(db, session_id, current_user.id)
    """Aggregate roll statistics for a session."""
    result = await db.execute(
        select(
            func.count(DMRoll.id).label("total"),
            func.avg(DMRoll.total).label("avg_roll"),
            func.max(DMRoll.total).label("highest"),
            func.min(DMRoll.total).label("lowest"),
        ).where(DMRoll.session_id == session_id)
    )
    row = result.one()

    # Nat 20 / Nat 1: count kept_rolls arrays containing a single max/min face value
    # We check total = 20 with num_dice=1 and faces=20 as a proxy for simplicity
    nat20_result = await db.execute(
        select(func.count(DMRoll.id)).where(
            DMRoll.session_id == session_id,
            DMRoll.num_dice == 1,
            DMRoll.dice_faces == "20",
            DMRoll.total == 20,
        )
    )
    nat1_result = await db.execute(
        select(func.count(DMRoll.id)).where(
            DMRoll.session_id == session_id,
            DMRoll.num_dice == 1,
            DMRoll.dice_faces == "20",
            DMRoll.total == 1,
        )
    )

    return RollStatsResponse(
        total_rolls=row.total or 0,
        nat20_count=nat20_result.scalar_one() or 0,
        nat1_count=nat1_result.scalar_one() or 0,
        avg_roll=float(row.avg_roll) if row.avg_roll is not None else None,
        highest=row.highest,
        lowest=row.lowest,
    )


# ── Game Events ───────────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/events", response_model=GameEventListResponse)
async def list_events(
    session_id: uuid.UUID,
    limit: int = 20,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GameEventListResponse:
    await _get_session_owned(db, session_id, current_user.id)
    count_result = await db.execute(
        select(func.count()).select_from(GameEvent).where(GameEvent.session_id == session_id)
    )
    total = count_result.scalar_one()

    result = await db.execute(
        select(GameEvent)
        .where(GameEvent.session_id == session_id)
        .order_by(GameEvent.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return GameEventListResponse(
        events=[GameEventResponse.model_validate(e) for e in result.scalars().all()],
        total=total,
    )


@router.get("/sessions/{session_id}/events/{event_id}", response_model=GameEventResponse)
async def get_event(
    session_id: uuid.UUID,
    event_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GameEventResponse:
    await _get_session_owned(db, session_id, current_user.id)
    result = await db.execute(
        select(GameEvent).where(
            GameEvent.id == event_id,
            GameEvent.session_id == session_id,
        )
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise AppError("Event not found", status_code=404, error_code="event_not_found")
    return GameEventResponse.model_validate(event)


@router.post("/sessions/{session_id}/answer", response_model=GameEventResponse)
async def answer_question(
    session_id: uuid.UUID,
    body: AnswerQuestionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GameEventResponse:
    """Store the player's answer to a DM ask_player question."""
    await _get_session_owned(db, session_id, current_user.id)
    result = await db.execute(
        select(GameEvent).where(
            GameEvent.id == body.event_id,
            GameEvent.session_id == session_id,
            GameEvent.ruling == "ask",
        )
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise AppError("Pending question not found", status_code=404, error_code="event_not_found")

    event.player_answer = body.answer
    await db.commit()
    await db.refresh(event)
    return GameEventResponse.model_validate(event)


# ── DM Config Attachments ─────────────────────────────────────────────────────


@router.post("/attachments", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def create_attachment(
    body: CreateAttachmentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttachmentResponse:
    if body.chat_id is not None:
        await get_chat(db, body.chat_id, current_user.id)
    if body.character_id is None and body.chat_id is None:
        raise AppError(
            "Either character_id or chat_id must be provided",
            status_code=422,
            error_code="attachment_target_required",
        )
    if body.character_id is not None and body.chat_id is not None:
        raise AppError(
            "Provide either character_id or chat_id, not both",
            status_code=422,
            error_code="attachment_target_exclusive",
        )

    ruleset_result = await db.execute(
        select(GameRuleset).where(
            GameRuleset.id == body.ruleset_id,
            GameRuleset.deleted_at.is_(None),
            or_(GameRuleset.user_id == current_user.id, GameRuleset.is_public.is_(True)),
        )
    )
    if ruleset_result.scalar_one_or_none() is None:
        raise AppError("Ruleset not found", status_code=404, error_code="ruleset_not_found")

    attachment = DMConfigAttachment(
        ruleset_id=body.ruleset_id,
        character_id=body.character_id,
        chat_id=body.chat_id,
        mode=body.mode,
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(attachment)
    return AttachmentResponse.model_validate(attachment)


@router.get("/attachments", response_model=list[AttachmentResponse])
async def list_attachments(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AttachmentResponse]:
    """List attachments owned by the user (i.e. that reference one of their rulesets)."""
    result = await db.execute(
        select(DMConfigAttachment)
        .join(GameRuleset, GameRuleset.id == DMConfigAttachment.ruleset_id)
        .where(
            or_(GameRuleset.user_id == current_user.id, GameRuleset.is_public.is_(True)),
            GameRuleset.deleted_at.is_(None),
        )
        .order_by(DMConfigAttachment.created_at.desc())
    )
    return [AttachmentResponse.model_validate(a) for a in result.scalars().all()]


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    attachment_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(
        select(DMConfigAttachment)
        .join(GameRuleset, GameRuleset.id == DMConfigAttachment.ruleset_id)
        .where(
            DMConfigAttachment.id == attachment_id,
            GameRuleset.user_id == current_user.id,
        )
    )
    attachment = result.scalar_one_or_none()
    if attachment is None:
        raise AppError("Attachment not found", status_code=404, error_code="attachment_not_found")

    await db.delete(attachment)
    await db.commit()


# ── DM Evaluation (SSE) ───────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/evaluate")
async def evaluate(
    session_id: uuid.UUID,
    body: EvaluateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Trigger the DM agent to evaluate a player action (SSE stream)."""
    session = await _get_session_owned(db, session_id, current_user.id)

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
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
