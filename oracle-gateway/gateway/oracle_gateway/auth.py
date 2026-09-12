"""Bearer token to user (PRINCIPLES P5, decision D8).

Tokens are opaque strings issued by the seed script; only their sha256 hex
lives in the database. Replacing this file with SSO or mTLS is the whole of
the auth swap.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from . import db


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    display_name: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token.strip()


async def current_user(request: Request) -> User:
    token = _bearer(request)
    row = await db.user_by_key_hash(hash_token(token))
    if row is None:
        raise HTTPException(status_code=401, detail="unknown or revoked token")
    return User(id=row["id"], email=row["email"], display_name=row["display_name"])


CurrentUser = Depends(current_user)
