"""campaign sender_mode + assigned wallets

Revision ID: 0005_campaign_sender_mode
Revises: 0004_quality
Create Date: 2026-05-06
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0005_campaign_sender_mode"
down_revision: Union[str, None] = "0004_quality"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-campaign sender mode: "multi" (default — use all assigned/active wallets
    # in parallel) or "single" (route every send through the first assigned/active
    # wallet, serializing nonce).
    op.add_column(
        "distribution_campaigns",
        sa.Column(
            "sender_mode",
            sa.String(length=16),
            nullable=False,
            server_default="multi",
        ),
    )

    # Many-to-many assignment of sender wallets to a campaign. When a campaign
    # has zero rows here, the worker falls back to ALL active wallets (legacy
    # behavior). When ≥1 row, only those wallets are used.
    op.create_table(
        "distribution_campaign_wallets",
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("distribution_campaigns.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "wallet_id",
            sa.Integer(),
            sa.ForeignKey("distribution_wallets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_dist_campaign_wallets_wallet",
        "distribution_campaign_wallets",
        ["wallet_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_dist_campaign_wallets_wallet",
        table_name="distribution_campaign_wallets",
    )
    op.drop_table("distribution_campaign_wallets")
    op.drop_column("distribution_campaigns", "sender_mode")
