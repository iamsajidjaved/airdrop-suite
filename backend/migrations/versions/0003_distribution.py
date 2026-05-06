"""distribution schema (Phase 2: token distribution)

Revision ID: 0003_distribution
Revises: 0002_seed_defaults
Create Date: 2026-05-06

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0003_distribution"
down_revision: Union[str, None] = "0002_seed_defaults"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Sender wallets we control. Private key is AES-GCM encrypted; KEK from env.
    op.create_table(
        "distribution_wallets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("address", sa.String(length=64), nullable=False, unique=True),
        sa.Column("label", sa.String(length=128), nullable=True),
        sa.Column("encrypted_private_key", sa.LargeBinary(), nullable=False),
        sa.Column("key_nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # A distribution campaign — one token, fixed amount per recipient.
    op.create_table(
        "distribution_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "token_id",
            sa.Integer(),
            sa.ForeignKey("airdrop_tokens.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount_per_recipient", sa.Numeric(38, 18), nullable=False),
        sa.Column("network", sa.String(length=32), nullable=False, server_default="ethereum"),
        # draft | ready | running | paused | completed | failed
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("recipient_filter", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("max_total_amount", sa.Numeric(38, 18), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dist_campaign_status", "distribution_campaigns", ["status"])

    # Recipients enumerated for a given campaign.
    op.create_table(
        "distribution_recipients",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("distribution_campaigns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("address", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(38, 18), nullable=False),
        # pending | sending | sent | confirmed | failed | skipped
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "assigned_wallet_id",
            sa.Integer(),
            sa.ForeignKey("distribution_wallets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("campaign_id", "address", name="uq_dist_recipient_campaign_address"),
    )
    op.create_index("ix_dist_recipient_campaign_status", "distribution_recipients", ["campaign_id", "status"])

    # One row per broadcast attempt (a recipient may have several across retries).
    op.create_table(
        "distribution_transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "recipient_id",
            sa.BigInteger(),
            sa.ForeignKey("distribution_recipients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("distribution_wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("nonce", sa.BigInteger(), nullable=False),
        sa.Column("tx_hash", sa.String(length=80), nullable=True, unique=True),
        sa.Column("max_fee_wei", sa.Numeric(38, 0), nullable=True),
        sa.Column("max_priority_wei", sa.Numeric(38, 0), nullable=True),
        sa.Column("gas_used", sa.BigInteger(), nullable=True),
        sa.Column("block_number", sa.BigInteger(), nullable=True),
        # broadcast | mined | success | reverted | dropped
        sa.Column("status", sa.String(length=32), nullable=False, server_default="broadcast"),
        sa.Column("broadcast_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_dist_tx_recipient", "distribution_transactions", ["recipient_id"])
    op.create_index("ix_dist_tx_status", "distribution_transactions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_dist_tx_status", table_name="distribution_transactions")
    op.drop_index("ix_dist_tx_recipient", table_name="distribution_transactions")
    op.drop_table("distribution_transactions")
    op.drop_index("ix_dist_recipient_campaign_status", table_name="distribution_recipients")
    op.drop_table("distribution_recipients")
    op.drop_index("ix_dist_campaign_status", table_name="distribution_campaigns")
    op.drop_table("distribution_campaigns")
    op.drop_table("distribution_wallets")
