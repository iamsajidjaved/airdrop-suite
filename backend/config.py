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

    # Database
    database_url: str

    # Airdrop monitor — fetch tuning (non-DB).
    # Token list and threshold live in the database (airdrop_tokens / airdrop_config tables).
    airdrop_page_size: int = 1000

    # Background scheduler: when enabled, the app spawns an asyncio task that
    # runs the monitor every `airdrop_scheduler_interval_seconds`. Because each
    # run resumes from `last_scanned_block`, no transactions are missed even if
    # the interval is large or the process restarts.
    airdrop_scheduler_enabled: bool = True
    airdrop_scheduler_interval_seconds: int = 60
    # Wait this many seconds after app startup before the first run, so the API
    # is responsive immediately and the first scan doesn't block boot.
    airdrop_scheduler_initial_delay_seconds: int = 5

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


# Global settings instance
settings = Settings()
