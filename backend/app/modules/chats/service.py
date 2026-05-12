"""Chats service."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.chats.models import Chat


async def create_chat(
    db: AsyncSession,
    user_id: uuid.UUID,
    character_id: uuid.UUID,
    title: str | None,
) -> Chat:
    chat = Chat(user_id=user_id, character_id=character_id, title=title)
    db.add(chat)
    await db.commit()
    await db.refresh(chat)
    return chat


async def list_chats(db: AsyncSession, user_id: uuid.UUID) -> list[Chat]:
    result = await db.execute(
        select(Chat)
        .where(Chat.user_id == user_id, Chat.deleted_at.is_(None))
        .order_by(Chat.updated_at.desc())
    )
    return list(result.scalars().all())


async def get_chat(
    db: AsyncSession, chat_id: uuid.UUID, user_id: uuid.UUID
) -> Chat:
    result = await db.execute(
        select(Chat).where(
            Chat.id == chat_id,
            Chat.user_id == user_id,
            Chat.deleted_at.is_(None),
        )
    )
    chat = result.scalar_one_or_none()
    if chat is None:
        raise AppError("Chat not found", status_code=404, error_code="chat_not_found")
    return chat


async def delete_chat(
    db: AsyncSession, chat_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    chat = await get_chat(db, chat_id, user_id)
    chat.deleted_at = datetime.now(timezone.utc)
    await db.commit()
