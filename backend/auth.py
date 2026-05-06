"""FastAPI dependency: shared-secret admin auth for distribution endpoints.

If `settings.airdrop_admin_token` is empty, auth is disabled (local dev mode).
Otherwise the request must include `X-Admin-Token: <token>`.
"""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from backend.config import settings


async def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = (settings.airdrop_admin_token or "").strip()
    if not expected:
        return  # auth disabled
    if not x_admin_token or x_admin_token.strip() != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Admin-Token header.",
        )
