"""quality filtering: blocklist + contract cache

Revision ID: 0004_quality
Revises: 0003_distribution
Create Date: 2026-05-06

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0004_quality"
down_revision: Union[str, None] = "0003_distribution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Minimal seed of universally-bad recipient addresses. Extend via the admin
# endpoint or add follow-up data migrations for curated CEX / router lists.
_BLOCKLIST_SEED = [
    # network, address (lowercase), reason, source
    ("ethereum", "0x0000000000000000000000000000000000000000", "zero_address", "seed"),
    ("ethereum", "0x000000000000000000000000000000000000dead", "burn_address", "seed"),
    ("ethereum", "0xffffffffffffffffffffffffffffffffffffffff", "sentinel", "seed"),
]


def upgrade() -> None:
    op.create_table(
        "quality_address_blocklist",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("network", sa.String(length=32), nullable=False, server_default="ethereum"),
        sa.Column("address", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("network", "address", name="uq_quality_blocklist_network_address"),
    )
    op.create_index(
        "ix_quality_blocklist_address", "quality_address_blocklist", ["address"]
    )

    op.create_table(
        "wallet_contract_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("network", sa.String(length=32), nullable=False, server_default="ethereum"),
        sa.Column("address", sa.String(length=64), nullable=False),
        sa.Column("is_contract", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("network", "address", name="uq_wallet_contract_cache_network_address"),
    )
    op.create_index(
        "ix_wallet_contract_cache_address", "wallet_contract_cache", ["address"]
    )

    # Seed the static blocklist.
    blocklist_table = sa.table(
        "quality_address_blocklist",
        sa.column("network", sa.String),
        sa.column("address", sa.String),
        sa.column("reason", sa.String),
        sa.column("source", sa.String),
    )
    op.bulk_insert(
        blocklist_table,
        [
            {"network": n, "address": a, "reason": r, "source": s}
            for (n, a, r, s) in _BLOCKLIST_SEED
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_wallet_contract_cache_address", table_name="wallet_contract_cache")
    op.drop_table("wallet_contract_cache")
    op.drop_index("ix_quality_blocklist_address", table_name="quality_address_blocklist")
    op.drop_table("quality_address_blocklist")
