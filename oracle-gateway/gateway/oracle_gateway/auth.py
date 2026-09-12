"""Who is writing, and may they (decision D8).

Two separate questions, deliberately:

  * **May you write?** One shared password, the same for everyone, sent as
    `Authorization: Bearer <password>`. There is nothing to provision and
    nothing to distribute per person.
  * **Whose session is this?** The `X-Oracle-User` header. Oracle exists to
    route context back to a person, so the log has to know whose work it is
    even though the password no longer says (PRINCIPLES P5).

The consequence is worth stating plainly: anyone holding the password can
claim to be anyone. That is a trusted-LAN assumption, listed with the rest of
the P10 debt. Replacing this file with SSO is the whole of the auth swap.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request

from . import db
from .config import SETTINGS


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    display_name: str


# Identities never change once created, so one lookup per process is enough.
_cache: dict[str, User] = {}


def display_name_for(identity: str) -> str:
    """'ada.lovelace@acme.com' -> 'Ada Lovelace'. Good enough to read in a list."""
    local = identity.split("@", 1)[0]
    words = [w for w in local.replace("_", ".").replace("-", ".").split(".") if w]
    return " ".join(w.capitalize() for w in words) or identity


def _check_password(request: Request) -> None:
    header = request.headers.get("authorization") or ""
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not supplied.strip():
        raise HTTPException(status_code=401, detail="missing password: send 'Authorization: Bearer <password>'")
    if not secrets.compare_digest(supplied.strip(), SETTINGS.password):
        raise HTTPException(status_code=401, detail="wrong password")


def _identity(request: Request) -> str:
    claimed = (request.headers.get(SETTINGS.user_header) or "").strip().lower()
    return claimed or SETTINGS.default_user


async def current_user(request: Request) -> User:
    _check_password(request)
    identity = _identity(request)

    cached = _cache.get(identity)
    if cached is not None:
        return cached

    row = await db.upsert_user(identity, display_name_for(identity))
    user = User(id=row["id"], email=row["email"], display_name=row["display_name"])
    _cache[identity] = user
    return user


CurrentUser = Depends(current_user)
