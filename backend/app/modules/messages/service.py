"""Messages service — store and retrieve chat messages."""

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.messages.models import Message, MessageRole


async def store_message(
    db: AsyncSession,
    chat_id: uuid.UUID,
    role: MessageRole,
    content: str,
    user_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> Message:
    msg = Message(
        chat_id=chat_id,
        user_id=user_id,
        role=role,
        content=content,
        metadata_=metadata,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)
    return msg


async def list_messages(
    db: AsyncSession,
    chat_id: uuid.UUID,
    limit: int = 50,
    before_id: uuid.UUID | None = None,
) -> tuple[list[Message], int]:
    where_clauses = [Message.chat_id == chat_id]

    if before_id:
        anchor = await db.get(Message, before_id)
        if anchor:
            where_clauses.append(Message.created_at < anchor.created_at)

    # Count only the rows that match the anchor filter so has_more is accurate
    count_q = select(func.count()).select_from(Message).where(*where_clauses)
    total = (await db.execute(count_q)).scalar_one()

    q = select(Message).where(*where_clauses).order_by(Message.created_at.desc()).limit(limit)
    result = await db.execute(q)
    msgs = list(reversed(result.scalars().all()))
    return msgs, total


async def get_recent_messages(
    db: AsyncSession, chat_id: uuid.UUID, limit: int = 50
) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))
