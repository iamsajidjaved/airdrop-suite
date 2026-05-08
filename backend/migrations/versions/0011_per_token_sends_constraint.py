"""Scope airdrop_sends uniqueness to (to_address, wallet_id, token_id).

Revision ID: 0011_per_token_sends_constraint
Revises: 0010_new_airdrop_worker
Create Date: 2026-05-08

The original constraint uq_airdrop_sends_address_wallet covered only
(to_address, wallet_id), which meant a confirmed send for token A would
permanently block a send for token B to the same recipient from the same
wallet.  Adding token_id to the constraint allows independent send records
per token, so switching the airdrop token correctly triggers a new round of
sends without touching the historical rows.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_per_token_sends_constraint"
down_revision: Union[str, None] = "0010_new_airdrop_worker"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_airdrop_sends_address_wallet",
        "airdrop_sends",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_airdrop_sends_address_wallet_token",
        "airdrop_sends",
        ["to_address", "wallet_id", "token_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_airdrop_sends_address_wallet_token",
        "airdrop_sends",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_airdrop_sends_address_wallet",
        "airdrop_sends",
        ["to_address", "wallet_id"],
    )
