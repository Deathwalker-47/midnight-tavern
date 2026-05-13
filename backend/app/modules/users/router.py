"""Users endpoints — profile and provider key management."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.auth.models import User
from app.modules.auth.schemas import UserResponse
from app.modules.auth.service import get_current_user
from app.modules.users.schemas import (
    ProviderKeyRequest,
    ProviderKeyResponse,
    UpdateProfileRequest,
)
from app.modules.users.service import (
    delete_provider_key,
    list_provider_keys,
    set_provider_key,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
async def get_profile(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    if body.display_name is not None:
        current_user.display_name = body.display_name
    if body.image_pref is not None:
        current_user.image_pref = body.image_pref
    await db.commit()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)


@router.get("/me/keys", response_model=list[ProviderKeyResponse])
async def list_keys(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProviderKeyResponse]:
    keys = await list_provider_keys(db, current_user.id)
    return [ProviderKeyResponse.model_validate(k) for k in keys]


@router.put("/me/keys/{provider}", response_model=ProviderKeyResponse)
async def upsert_key(
    provider: str,
    body: ProviderKeyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProviderKeyResponse:
    key_obj = await set_provider_key(db, current_user.id, provider, body.api_key)
    return ProviderKeyResponse.model_validate(key_obj)


@router.delete("/me/keys/{provider}", status_code=204)
async def remove_key(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await delete_provider_key(db, current_user.id, provider)
