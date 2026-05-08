"""Replace campaign-based distribution with simple airdrop_sends worker.

Revision ID: 0010_new_airdrop_worker
Revises: 0009_wallet_balance_cache
Create Date: 2026-05-08

Drops the campaign system (distribution_campaigns, distribution_campaign_wallets,
distribution_recipients, distribution_transactions) and replaces it with a single
airdrop_sends table that tracks which scanner-discovered addresses have been
processed by each active sender wallet.

Also seeds five new airdrop_config keys for worker settings.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0010_new_airdrop_worker"
down_revision: Union[str, None] = "0009_wallet_balance_cache"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop in FK-safe order (dependents first).
    op.drop_index("ix_dist_tx_status", table_name="distribution_transactions")
    op.drop_index("ix_dist_tx_recipient", table_name="distribution_transactions")
    op.drop_table("distribution_transactions")

    op.drop_index("ix_dist_recipient_campaign_status", table_name="distribution_recipients")
    op.drop_table("distribution_recipients")

    op.drop_index("ix_dist_campaign_wallets_wallet", table_name="distribution_campaign_wallets")
    op.drop_table("distribution_campaign_wallets")

    op.drop_index("ix_dist_campaign_status", table_name="distribution_campaigns")
    op.drop_table("distribution_campaigns")

    # New consolidated sends table.
    op.create_table(
        "airdrop_sends",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("to_address", sa.String(64), nullable=False),
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("airdrop_tokens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("distribution_wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("tx_hash", sa.String(80), nullable=True, unique=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("to_address", "wallet_id", name="uq_airdrop_sends_address_wallet"),
    )
    op.create_index("ix_airdrop_sends_to_address", "airdrop_sends", ["to_address"])
    op.create_index("ix_airdrop_sends_status", "airdrop_sends", ["status"])

    # Seed worker config keys.
    config_table = sa.table(
        "airdrop_config",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
    )
    op.bulk_insert(
        config_table,
        [
            {"key": "distribution_mode",            "value": "all"},
            {"key": "distribution_token_id",         "value": ""},
            {"key": "distribution_amount",           "value": "1.0"},
            {"key": "distribution_interval_seconds", "value": "10"},
            {"key": "distribution_max_retries",      "value": "3"},
        ],
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade from 0010 is not supported — it would permanently destroy campaign data."
    )
