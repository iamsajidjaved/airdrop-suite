"""Persist sender-wallet balances on the wallet row.

Revision ID: 0009_wallet_balance_cache
Revises: 0008_quality_from_filter
Create Date: 2026-05-08

Adds three columns to ``distribution_wallets``:

* ``eth_balance``         — last known ETH balance (Numeric, nullable)
* ``token_balances``      — last known ERC-20 balances as ``{symbol: amount}`` (JSONB)
* ``balances_updated_at`` — timestamp of the last successful balance refresh

Balances are written when a wallet is created and when the operator clicks
the per-row Refresh button. Listing wallets no longer hits the RPC.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0009_wallet_balance_cache"
down_revision: Union[str, None] = "0008_quality_from_filter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "distribution_wallets",
        sa.Column("eth_balance", sa.Numeric(38, 18), nullable=True),
    )
    op.add_column(
        "distribution_wallets",
        sa.Column("token_balances", JSONB, nullable=True),
    )
    op.add_column(
        "distribution_wallets",
        sa.Column("balances_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("distribution_wallets", "balances_updated_at")
    op.drop_column("distribution_wallets", "token_balances")
    op.drop_column("distribution_wallets", "eth_balance")
