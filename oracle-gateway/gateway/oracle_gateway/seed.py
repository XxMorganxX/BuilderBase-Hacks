"""Create users and issue API tokens.

    python -m oracle_gateway.seed alice@acme.com:Alice bob@acme.com:Bob
    SEED_USERS="alice@acme.com:Alice" python -m oracle_gateway.seed

Tokens are printed once and never stored in plaintext: only sha256 lands in
api_keys (decision D8). Re-running for an existing email issues an additional
key and leaves the old one working; pass --revoke-existing to retire them.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

import asyncpg

from .auth import hash_token
from .config import SETTINGS

TOKEN_PREFIX = "ork_"


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def parse_specs(values: list[str]) -> list[tuple[str, str]]:
    users: list[tuple[str, str]] = []
    for value in values:
        for spec in value.split(","):
            spec = spec.strip()
            if not spec:
                continue
            email, _, name = spec.partition(":")
            email = email.strip()
            if "@" not in email:
                raise SystemExit(f"not an email address: {spec!r} (expected email:Name)")
            users.append((email, name.strip() or email.split("@")[0].title()))
    return users


async def seed(users: list[tuple[str, str]], label: str, revoke_existing: bool) -> int:
    conn = await asyncpg.connect(SETTINGS.database_url)
    try:
        print(f"{'EMAIL':<28} {'NAME':<14} TOKEN")
        for email, name in users:
            user_id = await conn.fetchval(
                """
                INSERT INTO users (email, display_name) VALUES ($1, $2)
                ON CONFLICT (email) DO UPDATE SET display_name = EXCLUDED.display_name
                RETURNING id
                """,
                email,
                name,
            )
            if revoke_existing:
                await conn.execute(
                    "UPDATE api_keys SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
                    user_id,
                )
            token = new_token()
            await conn.execute(
                "INSERT INTO api_keys (user_id, key_hash, label) VALUES ($1, $2, $3)",
                user_id,
                hash_token(token),
                label,
            )
            print(f"{email:<28} {name:<14} {token}")
    finally:
        await conn.close()
    print("\nCopy these now. They are not recoverable: only their hashes are stored.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Oracle users and API tokens")
    parser.add_argument("users", nargs="*", help="email:Name (repeatable, or comma separated)")
    parser.add_argument("--label", default="seed", help="label recorded on the api_keys row")
    parser.add_argument(
        "--revoke-existing",
        action="store_true",
        help="revoke the user's current keys before issuing the new one",
    )
    args = parser.parse_args()

    specs = args.users or ([os.environ["SEED_USERS"]] if os.environ.get("SEED_USERS") else [])
    if not specs:
        parser.error("pass email:Name arguments or set SEED_USERS")
    users = parse_specs(specs)
    return asyncio.run(seed(users, args.label, args.revoke_existing))


if __name__ == "__main__":
    sys.exit(main())
