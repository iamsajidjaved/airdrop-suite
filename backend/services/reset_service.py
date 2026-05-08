"""Operational reset utilities.

Wipes collected data so the airdrop monitor and distribution worker can be
re-run cleanly against a different network (e.g. mainnet → Sepolia) or after
a configuration change to the quality filters.

Two scopes are supported:

* ``reset_collected_data`` (default) — clears every row produced at runtime
  (airdrop_sends, airdrop_transactions, wallet_contract_cache) while preserving
  operator-managed configuration (token list, threshold, blocklist, sender
  wallets, encrypted keys, distribution settings). Token ``last_scanned_block``
  is reset to NULL so the next monitor pass starts fresh.

* ``reset_collected_data(include_blocklist=True, include_wallets=True)`` —
  also wipes the quality blocklist (re-seeded by Alembic on the next migration
  pass) and the configured sender wallets. Use only when fully repurposing the
  installation; sender wallets cannot be recovered without their original
  private keys.

The schema itself is never dropped here — Alembic owns DDL.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from backend.db import async_session_factory

logger = logging.getLogger(__name__)


# Order matters: child tables before parents to satisfy FKs even though the
# truncates use CASCADE.
# airdrop_sends must come before airdrop_transactions (CASCADE FK on token_id).
_RUNTIME_TABLES_CASCADE = (
    "airdrop_sends",
    "airdrop_transactions",
    "wallet_contract_cache",
)


async def reset_collected_data(
    *,
    include_blocklist: bool = False,
    include_wallets: bool = False,
) -> dict[str, Any]:
    """Truncate runtime data and reset per-token scan progress.

    Returns a small report dict suitable for logging or API response.
    """
    cleared: list[str] = []
    async with async_session_factory() as session:
        # TRUNCATE ... RESTART IDENTITY CASCADE in one statement is atomic and
        # much faster than per-row DELETE on Postgres.
        tables = list(_RUNTIME_TABLES_CASCADE)
        if include_blocklist:
            tables.append("quality_address_blocklist")
        if include_wallets:
            # distribution_wallets has no FKs pointing in once recipients/txs
            # are gone, so it's safe to include here.
            tables.append("distribution_wallets")

        joined = ", ".join(tables)
        await session.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
        cleared.extend(tables)

        # Reset per-token scanner cursor so the next monitor run starts fresh.
        await session.execute(text("UPDATE airdrop_tokens SET last_scanned_block = NULL"))
        await session.commit()

    logger.warning(
        "Reset complete. Truncated tables=%s. last_scanned_block reset on all tokens.",
        cleared,
    )
    return {
        "ok": True,
        "tables_truncated": cleared,
        "tokens_reset": True,
    }
