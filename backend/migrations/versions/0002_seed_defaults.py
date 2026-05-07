"""seed default tokens and threshold

Revision ID: 0002_seed_defaults
Revises: 0001_initial
Create Date: 2026-05-06

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from backend.config import settings


revision: str = "0002_seed_defaults"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


tokens_table = sa.table(
    "airdrop_tokens",
    sa.column("symbol", sa.String),
    sa.column("contract_address", sa.String),
    sa.column("decimals", sa.Integer),
    sa.column("network", sa.String),
    sa.column("is_active", sa.Boolean),
)

config_table = sa.table(
    "airdrop_config",
    sa.column("key", sa.String),
    sa.column("value", sa.String),
)


def _parse_seed_tokens(raw: str) -> list[dict]:
    rows: list[dict] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            continue
        symbol, contract, decimals_str = parts
        rows.append({
            "symbol": symbol.strip().upper(),
            "contract_address": contract.strip().lower(),
            "decimals": int(decimals_str.strip()),
            "network": settings.network_environment,
            "is_active": True,
        })
    return rows


def upgrade() -> None:
    rows = _parse_seed_tokens(settings.airdrop_seed_tokens)
    if rows:
        op.bulk_insert(tokens_table, rows)
    op.bulk_insert(
        config_table,
        [{"key": "min_threshold_usd", "value": str(settings.airdrop_seed_threshold_usd)}],
    )


def downgrade() -> None:
    op.execute("DELETE FROM airdrop_config WHERE key = 'min_threshold_usd'")
    op.execute("DELETE FROM airdrop_tokens")
