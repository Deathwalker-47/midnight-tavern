"""Chats endpoints — CRUD + streaming message send with DM integration."""

import json
import uuid
from collections.abc import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.core.errors import AppError
from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.auth.service import get_current_user
from app.modules.chats.schemas import (
    ChatResponse,
    CreateChatRequest,
    UpdateChatRequest,
)
from app.modules.chats.service import (
    create_chat,
    delete_chat,
    get_chat,
    list_chats,
)
from app.modules.characters.service import get_character
from app.modules.messages.models import MessageRole
from app.modules.messages.schemas import (
    MessageListResponse,
    MessageResponse,
    SendMessageRequest,
)
from app.modules.messages.service import (
    get_recent_messages,
    list_messages,
    store_message,
)
from app.modules.dungeon_master.service import (
    evaluate_action,
    get_or_create_sheet,
    get_session_by_chat,
)
from app.modules.dungeon_master.schemas import DMVerdict

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/chats", tags=["chats"])


# ── Chat CRUD ─────────────────────────────────────────────────────────────────


@router.post("", response_model=ChatResponse, status_code=status.HTTP_201_CREATED)
async def create(
    body: CreateChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    # Verify character exists and is accessible
    await get_character(db, body.character_id, current_user.id)
    chat = await create_chat(db, current_user.id, body.character_id, body.title)
    return ChatResponse.model_validate(chat)


@router.get("", response_model=list[ChatResponse])
async def list_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChatResponse]:
    chats = await list_chats(db, current_user.id)
    return [ChatResponse.model_validate(c) for c in chats]


@router.get("/{chat_id}", response_model=ChatResponse)
async def get_one(
    chat_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    chat = await get_chat(db, chat_id, current_user.id)
    return ChatResponse.model_validate(chat)


@router.patch("/{chat_id}", response_model=ChatResponse)
async def update_title(
    chat_id: uuid.UUID,
    body: UpdateChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    chat = await get_chat(db, chat_id, current_user.id)
    if body.title is not None:
        chat.title = body.title
    await db.commit()
    await db.refresh(chat)
    return ChatResponse.model_validate(chat)


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    chat_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await delete_chat(db, chat_id, current_user.id)


# ── Messages ──────────────────────────────────────────────────────────────────


@router.get("/{chat_id}/messages", response_model=MessageListResponse)
async def list_msgs(
    chat_id: uuid.UUID,
    limit: int = 50,
    before_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageListResponse:
    await get_chat(db, chat_id, current_user.id)
    msgs, total = await list_messages(db, chat_id, limit=limit, before_id=before_id)
    return MessageListResponse(
        messages=[MessageResponse.model_validate(m) for m in msgs],
        total=total,
        has_more=total > limit,
    )


# ── Streaming message send ────────────────────────────────────────────────────


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: uuid.UUID,
    body: SendMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Send a user message and stream the response.

    SSE event stream:
      dm_thinking / dm_roll / dm_stat_update / dm_reject / dm_ask / dm_done
          — DM phase events (only if DM session active)
      token                 — character AI response token: {text: str}
      done                  — final event: {message_id: str, content: str}
      error                 — on failure: {code: str, message: str}
    """
    chat = await get_chat(db, chat_id, current_user.id)

    # Load character for system prompt
    character = await get_character(db, chat.character_id, current_user.id)

    # Store user message immediately (before streaming)
    user_msg = await store_message(
        db,
        chat_id=chat_id,
        role=MessageRole.user,
        content=body.content,
        user_id=current_user.id,
    )

    # Check for DM session
    dm_session = await get_session_by_chat(db, chat_id)

    async def event_stream() -> AsyncGenerator[str, None]:
        narrative_hint: str | None = None
        dm_rejected = False

        # ── DM evaluation phase ───────────────────────────────────────────────
        if dm_session and dm_session.is_active:
            sheet = await get_or_create_sheet(
                db, dm_session, character_id=None, display_name=current_user.display_name
            )
            recent = await get_recent_messages(db, chat_id, limit=10)
            recent_dicts = [
                {"role": m.role.value, "content": m.content}
                for m in recent
                if m.id != user_msg.id
            ]

            async for event in evaluate_action(
                db=db,
                session=dm_session,
                sheet=sheet,
                recent_messages=recent_dicts,
                player_action=body.content,
                message_id=user_msg.id,
            ):
                event_type = event["type"]
                event_data = json.dumps(event.get("data", {}))
                yield f"event: {event_type}\ndata: {event_data}\n\n"

                if event_type == "dm_done":
                    from app.modules.dungeon_master.schemas import DMEvalResult
                    result = DMEvalResult(**event["data"])
                    if result.verdict != DMVerdict.continue_:
                        dm_rejected = True
                    else:
                        narrative_hint = result.narrative_hint

        if dm_rejected:
            return

        # ── Story AI phase ────────────────────────────────────────────────────
        from app.core.config import settings
        from app.modules.providers.anthropic import AnthropicProvider
        from app.modules.users.service import get_provider_key

        api_key = await get_provider_key(db, current_user.id, "anthropic")
        if not api_key and settings.ANTHROPIC_API_KEY:
            api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            yield f"event: error\ndata: {json.dumps({'code': 'no_api_key', 'message': 'No Anthropic API key configured'})}\n\n"
            return

        provider = AnthropicProvider(api_key)

        # Build message history
        history = await get_recent_messages(db, chat_id, limit=40)
        provider_messages = [
            {"role": "user" if m.role == MessageRole.user else "assistant", "content": m.content}
            for m in history
            if m.role in (MessageRole.user, MessageRole.character)
        ]

        # Build system prompt
        system_parts = [character.system_prompt or f"You are {character.name}."]
        if character.personality:
            system_parts.append(f"\nPersonality: {character.personality}")
        if narrative_hint:
            system_parts.append(f"\n[DM ruling: {narrative_hint}]")
        system = "\n".join(system_parts)

        full_response = ""
        try:
            async for token in provider.stream(
                messages=provider_messages,
                system=system,
                model="claude-sonnet-4-6",
            ):
                full_response += token
                yield f"event: token\ndata: {json.dumps({'text': token})}\n\n"
        except Exception as exc:
            logger.error("stream_error", exc_info=exc, chat_id=str(chat_id))
            yield f"event: error\ndata: {json.dumps({'code': 'stream_error', 'message': str(exc)})}\n\n"
            return

        # Store assistant message
        ai_msg = await store_message(
            db,
            chat_id=chat_id,
            role=MessageRole.character,
            content=full_response,
        )

        # Auto-title chat from first exchange
        if not chat.title and len(history) <= 2:
            chat.title = body.content[:80]
            await db.commit()

        yield f"event: done\ndata: {json.dumps({'message_id': str(ai_msg.id), 'content': full_response})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
