from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from app.arcadedb import ArcadeSession
from app.db import User, get_session
from app.repositories.users import UserRepository
from app import activity
from app.services.providers import use_model

SECRET_KEY = os.getenv("SECRET_KEY", "")
ALGORITHM = "HS256"
# values that have appeared in this repo's code or examples: never valid signing keys
_KNOWN_WEAK_KEYS = {"", "change-me-in-production-use-32-chars-min", "change-me-to-a-random-32-char-string"}


def check_secret_key() -> None:
    """
    Refuse to start with a missing, published or short JWT signing key: anyone
    knowing it could mint a token for any account, admins included.
    """
    if SECRET_KEY in _KNOWN_WEAK_KEYS or len(SECRET_KEY) < 32:
        raise RuntimeError(
            "SECRET_KEY must be set to a random value of at least 32 characters. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )


ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: ArcadeSession = Depends(get_session),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: Optional[str] = payload.get("sub")
        if not user_id:
            raise credentials_error
    except JWTError:
        raise credentials_error

    user = await UserRepository(session).get(user_id)
    if not user:
        raise credentials_error
    await activity.touch_user(session.client, user)   # at most once an hour; see services/retention.py
    # every LLM call made while handling this request uses the user's chosen model
    use_model(user.get("llm_model"))
    return User(id=user["id"], username=user["username"], role=user.get("role") or "member",
                llm_model=user.get("llm_model"))
