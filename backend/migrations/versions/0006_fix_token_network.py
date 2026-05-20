"""fix airdrop_tokens network to match configured network_environment

Revision ID: 0006_fix_token_network
Revises: 0005_campaign_sender_mode
Create Date: 2026-05-07

Updates existing airdrop_tokens rows whose network is still the default
"ethereum" to use the value from settings.network_environment.  On a mainnet
deployment this is a no-op (network_environment == "mainnet" → kept as
"ethereum").  On Sepolia/Holesky deployments the rows are updated so the
worker's chain-ID validation and the recipient-filter's network join both work
correctly.

Also updates distribution_campaigns.network for existing campaigns that
inherited the wrong network from the token at creation time.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from backend.config import NETWORKS, settings

revision: str = "0006_fix_token_network"
down_revision: Union[str, None] = "0005_campaign_sender_mode"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _canonical_network() -> str:
    """Return the network key that matches the current settings."""
    env = (getattr(settings, "network_environment", "ethereum") or "ethereum").lower()
    if env in NETWORKS:
        return env
    chain = getattr(settings, "etherscan_chain_id", 1)
    for key, info in NETWORKS.items():
        if int(info["chain_id"]) == chain:
            return key
    return "ethereum"


def upgrade() -> None:
    network = _canonical_network()
    # Nothing to do when the configured network is the same as the seeded default.
    if network == "ethereum":
        return

    tokens_table = sa.table(
        "airdrop_tokens",
        sa.column("network", sa.String),
    )
    campaigns_table = sa.table(
        "distribution_campaigns",
        sa.column("network", sa.String),
    )

    op.execute(
        tokens_table.update()
        .where(tokens_table.c.network == "ethereum")
        .values(network=network)
    )
    op.execute(
        campaigns_table.update()
        .where(campaigns_table.c.network == "ethereum")
        .values(network=network)
    )


def downgrade() -> None:
    network = _canonical_network()
    if network == "ethereum":
        return

    tokens_table = sa.table(
        "airdrop_tokens",
        sa.column("network", sa.String),
    )
    campaigns_table = sa.table(
        "distribution_campaigns",
        sa.column("network", sa.String),
    )

    op.execute(
        tokens_table.update()
        .where(tokens_table.c.network == network)
        .values(network="ethereum")
    )
    op.execute(
        campaigns_table.update()
        .where(campaigns_table.c.network == network)
        .values(network="ethereum")
    )
