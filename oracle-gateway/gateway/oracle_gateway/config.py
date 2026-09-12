"""All environment-derived settings, read exactly once.

Single source of truth for every tunable in the gateway (PRINCIPLES P8).
Nothing else in the package reads os.environ.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Storage
    database_url: str
    db_pool_min: int
    db_pool_max: int
    db_command_timeout: float

    # Service
    port: int
    log_level: str

    # Ingest limits (PLAN 4.4)
    max_events_per_batch: int
    max_body_bytes: int
    content_text_max: int
    title_max: int

    # Read pagination
    default_page_limit: int
    max_page_limit: int


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql://oracle:oracle@localhost:5432/oracle"
        ),
        db_pool_min=_int("ORACLE_DB_POOL_MIN", 1),
        db_pool_max=_int("ORACLE_DB_POOL_MAX", 10),
        db_command_timeout=float(os.environ.get("ORACLE_DB_TIMEOUT", "30")),
        port=_int("ORACLE_GATEWAY_PORT", 8080),
        log_level=os.environ.get("ORACLE_LOG_LEVEL", "info").lower(),
        max_events_per_batch=_int("ORACLE_MAX_EVENTS_PER_BATCH", 1000),
        max_body_bytes=_int("ORACLE_MAX_BODY_BYTES", 10 * 1024 * 1024),
        content_text_max=_int("ORACLE_CONTENT_TEXT_MAX", 8192),
        title_max=_int("ORACLE_TITLE_MAX", 120),
        default_page_limit=_int("ORACLE_DEFAULT_PAGE_LIMIT", 500),
        max_page_limit=_int("ORACLE_MAX_PAGE_LIMIT", 1000),
    )


SETTINGS = load_settings()
