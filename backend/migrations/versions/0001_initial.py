"""initial airdrop schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-06

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "airdrop_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=32), nullable=False, unique=True),
        sa.Column("contract_address", sa.String(length=64), nullable=False, unique=True),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("network", sa.String(length=32), nullable=False, server_default="ethereum"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_scanned_block", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "airdrop_config",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "airdrop_transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tx_hash", sa.String(length=80), nullable=False),
        sa.Column("log_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("network", sa.String(length=32), nullable=False, server_default="ethereum"),
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("airdrop_tokens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_address", sa.String(length=64), nullable=False),
        sa.Column("to_address", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("amount_usd", sa.Numeric(38, 8), nullable=True),
        sa.Column("transferred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tx_hash", "log_index", "token_id", name="uq_airdrop_tx_hash_log_token"),
    )
    op.create_index("ix_airdrop_tx_token_block", "airdrop_transactions", ["token_id", "block_number"])
    op.create_index("ix_airdrop_tx_to", "airdrop_transactions", ["to_address"])
    op.create_index("ix_airdrop_tx_from", "airdrop_transactions", ["from_address"])
    op.create_index("ix_airdrop_tx_transferred_at", "airdrop_transactions", ["transferred_at"])


def downgrade() -> None:
    op.drop_index("ix_airdrop_tx_transferred_at", table_name="airdrop_transactions")
    op.drop_index("ix_airdrop_tx_from", table_name="airdrop_transactions")
    op.drop_index("ix_airdrop_tx_to", table_name="airdrop_transactions")
    op.drop_index("ix_airdrop_tx_token_block", table_name="airdrop_transactions")
    op.drop_table("airdrop_transactions")
    op.drop_table("airdrop_config")
    op.drop_table("airdrop_tokens")
