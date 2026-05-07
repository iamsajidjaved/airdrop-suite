"""seed sender-side quality filter settings into airdrop_config

Revision ID: 0008_quality_from_filter
Revises: 0007_quality_filter_settings
Create Date: 2026-05-07

Seeds two new quality-filter parameters:
  - quality_from_aggregator_drop_threshold: per-run sender frequency cap
  - quality_max_outbound_count: post-insert prune threshold for high-frequency senders

Uses ON CONFLICT DO NOTHING so the migration is safe to re-run.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from backend.config import settings


revision: str = "0008_quality_from_filter"
down_revision: Union[str, None] = "0007_quality_filter_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    seed_rows = [
        ("quality_from_aggregator_drop_threshold", str(settings.quality_from_aggregator_drop_threshold)),
        ("quality_max_outbound_count", str(settings.quality_max_outbound_count)),
    ]
    for key, value in seed_rows:
        op.execute(
            sa.text(
                "INSERT INTO airdrop_config (key, value) VALUES (:key, :value)"
                " ON CONFLICT (key) DO NOTHING"
            ).bindparams(key=key, value=value)
        )


def downgrade() -> None:
    keys = [
        "quality_from_aggregator_drop_threshold",
        "quality_max_outbound_count",
    ]
    op.execute(
        sa.text("DELETE FROM airdrop_config WHERE key = ANY(:keys)").bindparams(
            keys=keys
        )
    )
