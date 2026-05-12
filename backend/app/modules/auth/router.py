"""Auth endpoints: register, login, logout, me."""

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import COOKIE_NAME, ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token
from app.db.session import get_db
from app.modules.auth.schemas import LoginRequest, RegisterRequest, UserResponse
from app.modules.auth.service import authenticate_user, get_current_user, register_user
from app.modules.auth.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_MAX_AGE = ACCESS_TOKEN_EXPIRE_MINUTES * 60


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)) -> UserResponse:
    user = await register_user(db, body.username, body.email, body.password, body.display_name)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    user = await authenticate_user(db, body.username, body.password)
    token = create_access_token(str(user.id))
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # set to True behind HTTPS in production
    )
    return UserResponse.model_validate(user)


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)
