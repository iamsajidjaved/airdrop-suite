"""seed quality filter settings into airdrop_config

Revision ID: 0007_quality_filter_settings
Revises: 0006_fix_token_network
Create Date: 2026-05-07

Seeds the eight quality-filter parameters into the airdrop_config key-value
table so they can be edited via the UI without a server restart.  Each INSERT
uses ON CONFLICT DO NOTHING so the migration is safe to re-run if the keys
already exist (e.g. manually inserted earlier).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from backend.config import settings


revision: str = "0007_quality_filter_settings"
down_revision: Union[str, None] = "0006_fix_token_network"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    seed_rows = [
        ("quality_filter_enabled", str(settings.quality_filter_enabled).lower()),
        ("quality_contract_check_enabled", str(settings.quality_contract_check_enabled).lower()),
        ("quality_contract_check_concurrency", str(settings.quality_contract_check_concurrency)),
        ("quality_per_run_aggregator_drop_threshold", str(settings.quality_per_run_aggregator_drop_threshold)),
        ("quality_max_inbound_count", str(settings.quality_max_inbound_count)),
        ("quality_max_distinct_senders", str(settings.quality_max_distinct_senders)),
        ("quality_dormant_singleton_days", str(settings.quality_dormant_singleton_days)),
        ("quality_cross_token_aggregator_threshold", "0"),
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
        "quality_filter_enabled",
        "quality_contract_check_enabled",
        "quality_contract_check_concurrency",
        "quality_per_run_aggregator_drop_threshold",
        "quality_max_inbound_count",
        "quality_max_distinct_senders",
        "quality_dormant_singleton_days",
        "quality_cross_token_aggregator_threshold",
    ]
    op.execute(
        sa.text("DELETE FROM airdrop_config WHERE key = ANY(:keys)").bindparams(
            keys=keys
        )
    )
