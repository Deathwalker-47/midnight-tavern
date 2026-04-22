"""Characters endpoints."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.auth.service import get_current_user
from app.modules.characters.schemas import (
    CharacterResponse,
    CreateCharacterRequest,
    UpdateCharacterRequest,
)
from app.modules.characters.service import (
    create_character,
    delete_character,
    get_character,
    list_characters,
    update_character,
)

router = APIRouter(prefix="/characters", tags=["characters"])


@router.post("", response_model=CharacterResponse, status_code=status.HTTP_201_CREATED)
async def create(
    body: CreateCharacterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CharacterResponse:
    char = await create_character(
        db,
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        personality=body.personality,
        system_prompt=body.system_prompt,
        avatar_url=body.avatar_url,
        tags=body.tags,
        is_public=body.is_public,
    )
    return CharacterResponse.model_validate(char)


@router.get("", response_model=list[CharacterResponse])
async def list_all(
    public: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CharacterResponse]:
    chars = await list_characters(db, current_user.id, include_public=public)
    return [CharacterResponse.model_validate(c) for c in chars]


@router.get("/{character_id}", response_model=CharacterResponse)
async def get_one(
    character_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CharacterResponse:
    char = await get_character(db, character_id, current_user.id)
    return CharacterResponse.model_validate(char)


@router.patch("/{character_id}", response_model=CharacterResponse)
async def update(
    character_id: uuid.UUID,
    body: UpdateCharacterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CharacterResponse:
    updates = body.model_dump(exclude_none=True)
    char = await update_character(db, character_id, current_user.id, **updates)
    return CharacterResponse.model_validate(char)


@router.delete("/{character_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    character_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await delete_character(db, character_id, current_user.id)
