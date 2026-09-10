"""Email plus password, single workspace per user. Kept boring."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import base64
import hashlib

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from scripto.config import settings
from scripto.db import get_db
from scripto.models import User

_bearer = HTTPBearer(auto_error=False)


def _prehash(raw: str) -> bytes:
    """bcrypt silently truncates at 72 bytes, so hash to a fixed width first.

    base64 of a sha256 digest is 44 bytes and contains no NUL, which bcrypt
    would otherwise treat as a terminator.
    """
    return base64.b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(_prehash(raw), bcrypt.gensalt()).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(raw), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


def issue_token(user_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_ttl_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return user
