"""Auth service — registration, login, and current-user resolution."""

import uuid

import structlog
from fastapi import Cookie, Depends
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import (
    COOKIE_NAME,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.modules.auth.models import User

logger = structlog.get_logger(__name__)


async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(
        select(User).where(User.username == username, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    result = await db.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def register_user(
    db: AsyncSession, username: str, email: str, password: str, display_name: str | None
) -> User:
    existing = await db.execute(
        select(User).where(
            (User.username == username) | (User.email == email),
            User.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none():
        raise AppError(
            "Username or email already in use",
            status_code=409,
            error_code="user_already_exists",
        )

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        display_name=display_name,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("user_registered", user_id=str(user.id), username=username)
    return user


async def authenticate_user(
    db: AsyncSession, username: str, password: str
) -> User:
    user = await get_user_by_username(db, username)
    if not user or not verify_password(password, user.hashed_password):
        raise AppError(
            "Invalid username or password",
            status_code=401,
            error_code="invalid_credentials",
        )
    if not user.is_active:
        raise AppError("Account disabled", status_code=403, error_code="account_disabled")
    return user


async def get_current_user(
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not access_token:
        raise AppError("Not authenticated", status_code=401, error_code="not_authenticated")
    try:
        user_id_str = decode_access_token(access_token)
        user = await get_user_by_id(db, uuid.UUID(user_id_str))
    except (JWTError, ValueError):
        raise AppError("Invalid token", status_code=401, error_code="invalid_token")
    if not user:
        raise AppError("User not found", status_code=401, error_code="user_not_found")
    return user
