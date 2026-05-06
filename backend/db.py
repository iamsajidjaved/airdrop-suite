"""Async SQLAlchemy engine + session factory for Postgres (Neon)."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import settings

logger = logging.getLogger(__name__)


def _to_asyncpg_url(url: str) -> tuple[str, dict]:
    """Normalize a libpq-style Postgres URL for SQLAlchemy + asyncpg.

    Returns ``(url, connect_args)``. SQLAlchemy's asyncpg dialect does not
    forward libpq query params (``sslmode``, ``channel_binding``) to asyncpg,
    so we strip them and translate to an ``ssl`` connect-arg.
    """
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+asyncpg"
    elif scheme == "postgresql+psycopg2":
        scheme = "postgresql+asyncpg"

    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    new_pairs: list[tuple[str, str]] = []
    connect_args: dict = {}
    for key, value in query_pairs:
        k = key.lower()
        if k == "sslmode":
            v = value.lower()
            if v in ("require", "verify-ca", "verify-full"):
                connect_args["ssl"] = "require"
            elif v in ("disable", "allow", "prefer"):
                connect_args["ssl"] = v
            continue
        if k == "channel_binding":
            continue
        new_pairs.append((key, value))

    new_url = urlunsplit((scheme, parts.netloc, parts.path, urlencode(new_pairs), parts.fragment))
    return new_url, connect_args


def _to_sync_url(url: str) -> str:
    """Sync (psycopg2) URL for Alembic. Keeps libpq's sslmode as-is."""
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql+psycopg2"
    elif scheme.startswith("postgresql+asyncpg"):
        scheme = "postgresql+psycopg2"
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


ASYNC_DATABASE_URL, ASYNC_CONNECT_ARGS = _to_asyncpg_url(settings.database_url)
SYNC_DATABASE_URL = _to_sync_url(settings.database_url)

engine = create_async_engine(
    ASYNC_DATABASE_URL,
    connect_args=ASYNC_CONNECT_ARGS,
    pool_pre_ping=True,
    pool_recycle=300,
    echo=False,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an AsyncSession."""
    async with async_session_factory() as session:
        yield session
