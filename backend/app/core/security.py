"""JWT, password hashing, and encryption utilities."""

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ACCESS_TOKEN_EXPIRE_MINUTES = 60
ALGORITHM = "HS256"
COOKIE_NAME = "access_token"


# ── Passwords ─────────────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ── JWT ───────────────────────────────────────────────────────────────────────


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str:
    """Decode JWT and return subject (user_id). Raises JWTError on failure."""
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    sub: str | None = payload.get("sub")
    if sub is None:
        raise JWTError("Missing subject")
    return sub


# ── Provider key encryption ───────────────────────────────────────────────────


def _get_fernet() -> Fernet:
    key_bytes = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def encrypt_provider_key(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_provider_key(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
