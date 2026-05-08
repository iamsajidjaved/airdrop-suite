"""Configuration management using pydantic-settings"""
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

    # Network selection. Etherscan v2 is a unified endpoint keyed by chain id:
    #   1         = Ethereum mainnet
    #   11155111  = Sepolia testnet
    #   17000     = Holesky testnet
    # Switch to a testnet by setting ETHERSCAN_CHAIN_ID and pointing
    # ETH_RPC_URL at the matching JSON-RPC node. The admin UI (or
    # AIRDROP_SEED_TOKENS) must also be re-pointed at testnet token contracts;
    # mainnet contract addresses do not exist on Sepolia/Holesky.
    etherscan_chain_id: int = 1
    # Free-form label surfaced in logs / status payloads. Purely informational
    # ("mainnet", "sepolia", "holesky", ...). Defaults are inferred but can be
    # overridden via env.
    network_environment: str = "mainnet"

    # Database
    database_url: str

    # Airdrop monitor — fetch tuning (non-DB).
    # Token list and threshold live in the database (airdrop_tokens / airdrop_config tables).
    airdrop_page_size: int = 1000

    # Background scheduler is intentionally disabled. The Scanner
    # runs ONLY when the operator clicks "Run Scan" in the admin UI (or hits
    # POST /api/airdrop/monitor/run). No cron, no auto-loop.

    # Bootstrap defaults used by the initial Alembic seed migration only.
    airdrop_seed_tokens: str = (
        "USDT:0xdac17f958d2ee523a2206206994597c13d831ec7:6,"
        "USDC:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48:6"
    )
    airdrop_seed_threshold_usd: float = 500.0

    # ---------------- Phase 2: Token Distribution ----------------
    # JSON-RPC endpoint used for signing/broadcasting (Alchemy/Infura/etc.).
    # Required before the distribution worker can send anything.
    eth_rpc_url: str = ""

    # Base64-encoded 32-byte AES key used to encrypt sender wallet private keys
    # at rest. Required before any wallet can be added. Generate one with:
    #   python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
    airdrop_kek: str = ""

    # Shared secret required on all admin write endpoints (sent as
    # `X-Admin-Token` header). Empty = auth disabled (local dev only).
    airdrop_admin_token: str = ""

    # Distribution worker — opt-in. Must be started from the admin UI.
    distribution_worker_enabled: bool = False
    distribution_worker_interval_seconds: int = 5
    distribution_receipt_poll_seconds: int = 12
    distribution_max_retries_per_recipient: int = 3
    # Hard cap on EIP-1559 max fee. Sends above this gas price are skipped.
    distribution_max_gas_price_gwei: float = 100.0
    # Per-wallet daily spend cap (in token units, not USD). 0 = unlimited.
    distribution_per_wallet_daily_cap: float = 0.0
    # Number of recipients sent in parallel across the wallet pool per tick.
    distribution_max_inflight: int = 8

    # ---------------- Quality filter (recipient pruning) ----------------
    # Master switch. When False, the monitor stores every transfer regardless
    # of quality (legacy behavior).
    quality_filter_enabled: bool = True
    # Inline `eth_getCode` lookups during the monitor pass. Requires
    # ETH_RPC_URL. Disabled automatically (with a warning) if web3 is not
    # configured. Cached per address forever in `wallet_contract_cache`.
    quality_contract_check_enabled: bool = True
    quality_contract_check_concurrency: int = 4
    # Per-run aggregator drop: any to_address that receives more than this many
    # transfers within a single monitor batch is treated as an exchange/router
    # and all of its rows in that batch are dropped before insert.
    quality_per_run_aggregator_drop_threshold: int = 50
    # Symmetric sender-side drop: if one from_address sends to more than this
    # many distinct to_addresses in a single batch, all its rows are dropped
    # (catches faucets / batch-distribution bots within a single run).
    quality_from_aggregator_drop_threshold: int = 50
    # Aggregate prune (post-insert) thresholds. Addresses exceeding either of
    # these in the cumulative airdrop_transactions table are deleted.
    quality_max_inbound_count: int = 1000
    quality_max_distinct_senders: int = 500
    # Post-insert sender prune: delete all transactions whose from_address has
    # distributed to more than this many distinct recipients across all runs.
    quality_max_outbound_count: int = 500
    # Singleton wallets older than this many days with only one inbound tx are
    # pruned as low-engagement. Recent singletons get a chance to accumulate.
    quality_dormant_singleton_days: int = 180

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


# Global settings instance
settings = Settings()


# ----- Network registry -----
# Single source of truth that maps the per-token `network` string (stored in
# `airdrop_tokens.network`) to the Etherscan v2 chain id used at scan time and
# to the human label shown in the UI. Add new networks here.
NETWORKS: dict[str, dict] = {
    "ethereum": {"chain_id": 1, "label": "Ethereum Mainnet", "explorer": "https://etherscan.io"},
    "sepolia":  {"chain_id": 11155111, "label": "Sepolia Testnet", "explorer": "https://sepolia.etherscan.io"},
}


def chain_id_for(network: str) -> int:
    """Return the Etherscan v2 chain id for a token network. Falls back to the
    configured ``etherscan_chain_id`` for unknown networks (forward-compat)."""
    info = NETWORKS.get((network or "").lower())
    return int(info["chain_id"]) if info else int(settings.etherscan_chain_id)

