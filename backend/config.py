"""Configuration management using pydantic-settings"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # API Keys
    etherscan_api_key: str
    trongrid_api_key: str

    # Server Configuration
    host: str = "127.0.0.1"
    port: int = 8000

    # API Endpoints
    etherscan_base_url: str = "https://api.etherscan.io/v2/api"
    trongrid_base_url: str = "https://api.trongrid.io"

    # Database
    database_url: str

    # Airdrop monitor — fetch tuning (non-DB).
    # The active network, token list, threshold, and iGaming brands all live in
    # the database (airdrop_config / airdrop_tokens / igaming_brands).
    airdrop_page_size: int = 1000

    # Bootstrap defaults used by the initial Alembic seed migration only.
    airdrop_seed_tokens: str = (
        "USDT:0xdac17f958d2ee523a2206206994597c13d831ec7:6,"
        "USDC:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48:6"
    )
    airdrop_seed_threshold_usd: float = 500.0

    # ---------------- Phase 2: Token Distribution ----------------
    eth_rpc_url: str = ""
    airdrop_kek: str = ""
    airdrop_admin_token: str = ""
    distribution_worker_enabled: bool = False
    distribution_worker_interval_seconds: int = 5
    distribution_receipt_poll_seconds: int = 12
    distribution_max_retries_per_recipient: int = 3
    distribution_max_gas_price_gwei: float = 100.0
    distribution_per_wallet_daily_cap: float = 0.0
    distribution_max_inflight: int = 8

    # ---------------- Quality filter (recipient pruning) ----------------
    quality_filter_enabled: bool = True
    quality_contract_check_enabled: bool = True
    quality_contract_check_concurrency: int = 4
    quality_per_run_aggregator_drop_threshold: int = 50
    quality_from_aggregator_drop_threshold: int = 50
    quality_max_inbound_count: int = 1000
    quality_max_distinct_senders: int = 500
    quality_max_outbound_count: int = 500
    quality_dormant_singleton_days: int = 180

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # silently ignore old env vars (e.g. ETHERSCAN_CHAIN_ID, NETWORK_ENVIRONMENT)
    )


# Global settings instance
settings = Settings()


# ----- Network registry -----
# Maps the network key (stored in airdrop_config as active_network) to
# the Etherscan v2 chain id and UI metadata. Add new networks here.
NETWORKS: dict[str, dict] = {
    "ethereum": {"chain_id": 1, "label": "Ethereum Mainnet", "explorer": "https://etherscan.io"},
    "sepolia":  {"chain_id": 11155111, "label": "Sepolia Testnet", "explorer": "https://sepolia.etherscan.io"},
}

DEFAULT_NETWORK = "ethereum"


def chain_id_for(network: str) -> int:
    """Return the Etherscan v2 chain id for a network key. Falls back to Ethereum mainnet."""
    info = NETWORKS.get((network or "").lower())
    return int(info["chain_id"]) if info else 1


async def get_active_network(session) -> str:
    """Read the global active_network from airdrop_config. Falls back to 'ethereum'."""
    from sqlalchemy import select
    from backend.db_models import AirdropConfig

    row = await session.scalar(
        select(AirdropConfig.value).where(AirdropConfig.key == "active_network")
    )
    if row and row.strip().lower() in NETWORKS:
        return row.strip().lower()
    return DEFAULT_NETWORK
