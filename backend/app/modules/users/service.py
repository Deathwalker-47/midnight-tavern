"""Users service — profile management and encrypted provider key storage."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import decrypt_provider_key, encrypt_provider_key
from app.modules.users.models import ProviderKey


async def set_provider_key(
    db: AsyncSession, user_id: uuid.UUID, provider: str, api_key: str
) -> ProviderKey:
    result = await db.execute(
        select(ProviderKey).where(
            ProviderKey.user_id == user_id, ProviderKey.provider == provider
        )
    )
    key_obj = result.scalar_one_or_none()
    encrypted = encrypt_provider_key(api_key)
    if key_obj:
        key_obj.encrypted_key = encrypted
    else:
        key_obj = ProviderKey(
            user_id=user_id, provider=provider, encrypted_key=encrypted
        )
        db.add(key_obj)
    await db.commit()
    await db.refresh(key_obj)
    return key_obj


async def get_provider_key(
    db: AsyncSession, user_id: uuid.UUID, provider: str
) -> str | None:
    result = await db.execute(
        select(ProviderKey).where(
            ProviderKey.user_id == user_id, ProviderKey.provider == provider
        )
    )
    key_obj = result.scalar_one_or_none()
    if not key_obj:
        return None
    return decrypt_provider_key(key_obj.encrypted_key)


async def list_provider_keys(
    db: AsyncSession, user_id: uuid.UUID
) -> list[ProviderKey]:
    result = await db.execute(
        select(ProviderKey).where(ProviderKey.user_id == user_id)
    )
    return list(result.scalars().all())


async def delete_provider_key(
    db: AsyncSession, user_id: uuid.UUID, provider: str
) -> None:
    result = await db.execute(
        select(ProviderKey).where(
            ProviderKey.user_id == user_id, ProviderKey.provider == provider
        )
    )
    key_obj = result.scalar_one_or_none()
    if key_obj is None:
        raise AppError("Provider key not found", status_code=404, error_code="key_not_found")
    await db.delete(key_obj)
    await db.commit()
