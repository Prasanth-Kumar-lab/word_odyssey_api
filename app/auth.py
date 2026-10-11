"""Bearer Token JWT authentication for Word Odyssey FastAPI."""
from __future__ import annotations

import datetime
from typing import Any, Dict, Optional
import jwt
from fastapi import Header, HTTPException, status

from . import db
from .config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_DAYS


def create_bearer_token(user_id: str, phone: str) -> str:
    exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=JWT_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "phone": phone,
        "iat": int(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        "exp": exp,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_bearer_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token has expired. Please sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    db.init_db()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_bearer_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject.",
        )
    user = db.users_col.find_one({"id": user_id})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found.",
        )
    # Update active presence timestamp on every authenticated API request
    db.users_col.update_one(
        {"id": user_id},
        {"$set": {"lastActiveAt": db.now_ms(), "isOnline": True}},
    )
    user["lastActiveAt"] = db.now_ms()
    user["isOnline"] = True
    return user


def user_from_ws_token(token: str) -> Dict[str, Any]:
    db.init_db()
    clean = token.replace("Bearer ", "").replace("bearer ", "").strip()
    payload = decode_bearer_token(clean)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.users_col.find_one({"id": user_id})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
