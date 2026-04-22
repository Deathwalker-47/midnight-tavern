"""Character CRUD service."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.characters.models import Character


async def create_character(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    description: str | None,
    personality: str | None,
    system_prompt: str | None,
    avatar_url: str | None,
    tags: list[str],
    is_public: bool,
) -> Character:
    char = Character(
        user_id=user_id,
        name=name,
        description=description,
        personality=personality,
        system_prompt=system_prompt,
        avatar_url=avatar_url,
        tags=tags,
        is_public=is_public,
    )
    db.add(char)
    await db.commit()
    await db.refresh(char)
    return char


async def list_characters(
    db: AsyncSession, user_id: uuid.UUID, include_public: bool = False
) -> list[Character]:
    if include_public:
        condition = or_(Character.user_id == user_id, Character.is_public.is_(True))
    else:
        condition = Character.user_id == user_id
    result = await db.execute(
        select(Character)
        .where(condition, Character.deleted_at.is_(None))
        .order_by(Character.created_at.desc())
    )
    return list(result.scalars().all())


async def get_character(
    db: AsyncSession, character_id: uuid.UUID, user_id: uuid.UUID
) -> Character:
    result = await db.execute(
        select(Character).where(
            Character.id == character_id,
            Character.deleted_at.is_(None),
            or_(Character.user_id == user_id, Character.is_public.is_(True)),
        )
    )
    char = result.scalar_one_or_none()
    if char is None:
        raise AppError("Character not found", status_code=404, error_code="character_not_found")
    return char


async def update_character(
    db: AsyncSession, character_id: uuid.UUID, user_id: uuid.UUID, **kwargs: object
) -> Character:
    char = await get_character(db, character_id, user_id)
    if char.user_id != user_id:
        raise AppError("Forbidden", status_code=403, error_code="forbidden")
    for field, value in kwargs.items():
        if value is not None:
            setattr(char, field, value)
    await db.commit()
    await db.refresh(char)
    return char


async def delete_character(
    db: AsyncSession, character_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    char = await get_character(db, character_id, user_id)
    if char.user_id != user_id:
        raise AppError("Forbidden", status_code=403, error_code="forbidden")
    char.deleted_at = datetime.now(timezone.utc)
    await db.commit()
