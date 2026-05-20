"""Add iGaming brands, scan_mode to transactions, drop per-token network, centralise network config.

Revision ID: 0012_igaming_brands
Revises: 0011_per_token_sends_constraint
Create Date: 2026-05-20

Changes:
- New table: igaming_brands — tracks competitor iGaming platform wallets whose
  outgoing ERC-20 transfers are used to identify target users.
- airdrop_transactions: add scan_mode (standard|igaming) and igaming_brand_id FK.
- airdrop_tokens: drop network column (replaced by global active_network config key).
- airdrop_config: seed active_network and igaming_threshold_usd.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_igaming_brands"
down_revision: Union[str, None] = "0011_per_token_sends_constraint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create igaming_brands table
    op.create_table(
        "igaming_brands",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("wallet_address", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_scanned_block", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_unique_constraint("uq_igaming_brands_wallet", "igaming_brands", ["wallet_address"])
    op.create_index("ix_igaming_brands_wallet_address", "igaming_brands", ["wallet_address"])

    # 2. Add scan_mode + igaming_brand_id to airdrop_transactions
    op.add_column(
        "airdrop_transactions",
        sa.Column(
            "scan_mode",
            sa.String(20),
            nullable=False,
            server_default="standard",
        ),
    )
    op.add_column(
        "airdrop_transactions",
        sa.Column("igaming_brand_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_airdrop_tx_igaming_brand",
        "airdrop_transactions",
        "igaming_brands",
        ["igaming_brand_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_airdrop_tx_scan_mode", "airdrop_transactions", ["scan_mode"])

    # 3. Drop network column from airdrop_tokens (now global via airdrop_config)
    op.drop_column("airdrop_tokens", "network")

    # 4. Seed new airdrop_config keys
    op.execute(
        sa.text(
            """
            INSERT INTO airdrop_config (key, value, updated_at)
            VALUES
                ('active_network',        'ethereum', now()),
                ('igaming_threshold_usd', '0',        now())
            ON CONFLICT (key) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Remove seeded config keys
    op.execute(
        sa.text(
            "DELETE FROM airdrop_config WHERE key IN ('active_network', 'igaming_threshold_usd')"
        )
    )

    # Restore network column on airdrop_tokens (fill with 'ethereum')
    op.add_column(
        "airdrop_tokens",
        sa.Column(
            "network",
            sa.String(32),
            nullable=False,
            server_default="ethereum",
        ),
    )

    # Remove scan_mode / igaming_brand_id from airdrop_transactions
    op.drop_constraint("fk_airdrop_tx_igaming_brand", "airdrop_transactions", type_="foreignkey")
    op.drop_index("ix_airdrop_tx_scan_mode", table_name="airdrop_transactions")
    op.drop_column("airdrop_transactions", "igaming_brand_id")
    op.drop_column("airdrop_transactions", "scan_mode")

    # Drop igaming_brands table
    op.drop_index("ix_igaming_brands_wallet_address", table_name="igaming_brands")
    op.drop_constraint("uq_igaming_brands_wallet", "igaming_brands", type_="unique")
    op.drop_table("igaming_brands")
