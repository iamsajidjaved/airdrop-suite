"""Pydantic models for request/response validation"""
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class WalletValidationRequest(BaseModel):
    """Request model for wallet validation"""
    address: str = Field(..., description="Wallet address to validate")


class WalletValidationResponse(BaseModel):
    """Response model for wallet validation"""
    valid: bool
    network: Optional[Literal["ethereum", "tron"]] = None
    message: str


class Transaction(BaseModel):
    """Transaction model"""
    hash: str
    network: Literal["ERC", "TRC"]
    timestamp: int
    datetime: str
    from_address: str = Field(..., alias="from")
    to_address: str = Field(..., alias="to")
    amount: str
    token_symbol: str
    token_contract: Optional[str] = None
    direction: Literal["incoming", "outgoing"]
    status: str
    block_number: int
    gas_fee: Optional[str] = None

    class Config:
        populate_by_name = True


class TransactionResponse(BaseModel):
    """Response model for transaction queries"""
    wallet_address: str
    total_transactions: int
    networks: list[str]
    first_seen: Optional[str] = None
    last_activity: Optional[str] = None
    transactions: list[Transaction]


class ErrorResponse(BaseModel):
    """Error response model"""
    error: str
    detail: Optional[str] = None


class MonitorRunResult(BaseModel):
    tokens_scanned: list[str]
    brands_scanned: list[str] = Field(default_factory=list)
    new_transfers_inserted: int
    total_transfers_stored: int
    blocks_scanned_per_token: dict[str, dict]
    blocks_scanned_per_brand: dict[str, dict] = Field(default_factory=dict)
    run_timestamp: str
    errors: list[str]


class AirdropStatusResponse(BaseModel):
    last_run_timestamp: Optional[str]
    last_block_per_token: dict[str, int]
    last_block_per_brand: dict[str, int] = Field(default_factory=dict)
    total_transfers: int
    scan_mode_breakdown: dict[str, int] = Field(default_factory=dict)


# ----- iGaming Brands CRUD -----

import re as _re

_HEX_ADDR_RE_BRAND = _re.compile(r"0x[0-9a-f]{40}")


class IgamingBrandBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    wallet_address: str = Field(..., min_length=42, max_length=64)
    description: Optional[str] = Field(None, max_length=255)
    is_active: bool = True

    @field_validator("wallet_address")
    @classmethod
    def _validate_wallet(cls, v: str) -> str:
        v = v.strip().lower()
        if not _HEX_ADDR_RE_BRAND.fullmatch(v):
            raise ValueError("wallet_address must be a 0x-prefixed 40-hex-char Ethereum address")
        return v


class IgamingBrandCreate(IgamingBrandBase):
    pass


class IgamingBrandUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None
    last_scanned_block: Optional[int] = Field(None, ge=0)


class IgamingBrandOut(IgamingBrandBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    last_scanned_block: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    transaction_count: int = 0


# ----- Token CRUD -----

class AirdropTokenBase(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    contract_address: str = Field(..., min_length=42, max_length=64)
    decimals: int = Field(..., ge=0, le=36)
    is_active: bool = True

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("contract_address")
    @classmethod
    def _validate_contract(cls, v: str) -> str:
        v = v.strip().lower()
        import re
        if not re.fullmatch(r"0x[0-9a-f]{40}", v):
            raise ValueError("contract_address must be a 0x-prefixed 40-hex-char address")
        return v


class AirdropTokenCreate(AirdropTokenBase):
    pass


class AirdropTokenUpdate(BaseModel):
    symbol: Optional[str] = Field(None, min_length=1, max_length=32)
    contract_address: Optional[str] = Field(None, min_length=42, max_length=64)
    decimals: Optional[int] = Field(None, ge=0, le=36)
    is_active: Optional[bool] = None
    last_scanned_block: Optional[int] = Field(None, ge=0)

    @field_validator("symbol")
    @classmethod
    def _upper_symbol(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().upper() if v else v

    @field_validator("contract_address")
    @classmethod
    def _validate_contract(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        import re
        v = v.strip().lower()
        if not re.fullmatch(r"0x[0-9a-f]{40}", v):
            raise ValueError("contract_address must be a 0x-prefixed 40-hex-char address")
        return v


class AirdropTokenOut(AirdropTokenBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    last_scanned_block: Optional[int] = None
    created_at: datetime
    updated_at: datetime


# ----- Config -----

class AirdropConfigOut(BaseModel):
    min_threshold_usd: float
    active_network: str = "ethereum"
    igaming_threshold_usd: float = 0.0


class AirdropConfigUpdate(BaseModel):
    min_threshold_usd: Optional[float] = Field(None, gt=0)
    active_network: Optional[str] = Field(None, max_length=32)
    igaming_threshold_usd: Optional[float] = Field(None, ge=0)


# ----- Quality filter settings -----

class QualityFilterSettings(BaseModel):
    quality_filter_enabled: bool = True
    quality_contract_check_enabled: bool = True
    quality_contract_check_concurrency: int = Field(4, ge=1, le=32)
    quality_per_run_aggregator_drop_threshold: int = Field(50, ge=0)
    quality_from_aggregator_drop_threshold: int = Field(50, ge=0)
    quality_max_inbound_count: int = Field(1000, ge=1)
    quality_max_distinct_senders: int = Field(500, ge=1)
    quality_max_outbound_count: int = Field(500, ge=1)
    quality_dormant_singleton_days: int = Field(180, ge=1)
    quality_cross_token_aggregator_threshold: int = Field(0, ge=0)


class QualityFilterUpdate(BaseModel):
    quality_filter_enabled: Optional[bool] = None
    quality_contract_check_enabled: Optional[bool] = None
    quality_contract_check_concurrency: Optional[int] = Field(None, ge=1, le=32)
    quality_per_run_aggregator_drop_threshold: Optional[int] = Field(None, ge=0)
    quality_from_aggregator_drop_threshold: Optional[int] = Field(None, ge=0)
    quality_max_inbound_count: Optional[int] = Field(None, ge=1)
    quality_max_distinct_senders: Optional[int] = Field(None, ge=1)
    quality_max_outbound_count: Optional[int] = Field(None, ge=1)
    quality_dormant_singleton_days: Optional[int] = Field(None, ge=1)
    quality_cross_token_aggregator_threshold: Optional[int] = Field(None, ge=0)


# ----- Transactions -----

class AirdropTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    tx_hash: str
    log_index: int
    block_number: int
    network: str
    token_id: int
    token_symbol: Optional[str] = None
    from_address: str
    to_address: str
    amount: Decimal
    amount_usd: Optional[Decimal] = None
    transferred_at: datetime
    scan_mode: str = "standard"
    igaming_brand_id: Optional[int] = None
    brand_name: Optional[str] = None
    created_at: datetime


class AirdropTransactionListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AirdropTransactionOut]


# ============================================================
#                Phase 2: Token Distribution
# ============================================================

import re

_HEX_ADDR_RE = re.compile(r"0x[0-9a-f]{40}")


def _norm_address(v: str) -> str:
    v = v.strip().lower()
    if not _HEX_ADDR_RE.fullmatch(v):
        raise ValueError("address must be a 0x-prefixed 40-hex-char Ethereum address")
    return v


# ----- Wallets -----

class DistributionWalletCreate(BaseModel):
    private_key: str = Field(..., description="0x-prefixed 64-hex-char private key. Stored encrypted.")
    label: Optional[str] = Field(None, max_length=128)


class DistributionWalletUpdate(BaseModel):
    label: Optional[str] = Field(None, max_length=128)
    is_active: Optional[bool] = None


class DistributionWalletOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    address: str
    label: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    eth_balance: Optional[Decimal] = None
    token_balances: Optional[dict[str, Decimal]] = None
    balances_updated_at: Optional[datetime] = None


# ----- Worker -----

class DistributionWorkerState(BaseModel):
    enabled: bool
    running: bool
    interval_seconds: int
    mode: str = "all"
    last_tick_at: Optional[str] = None
    last_error: Optional[str] = None
    in_flight: int = 0
    sent_total: int = 0
    confirmed_total: int = 0
    failed_total: int = 0
    pending_sends: int = 0


# ----- Config (DB-backed) -----

class DistributionConfigOut(BaseModel):
    distribution_mode: str
    distribution_token_id: Optional[int] = None
    distribution_amount: str
    distribution_interval_seconds: int
    distribution_max_retries: int
    eth_rpc_configured: bool
    kek_configured: bool


class DistributionConfigUpdate(BaseModel):
    distribution_mode: Optional[Literal["all", "random"]] = None
    distribution_token_id: Optional[int] = None
    distribution_amount: Optional[str] = None
    distribution_interval_seconds: Optional[int] = Field(None, ge=1)
    distribution_max_retries: Optional[int] = Field(None, ge=1)


# ----- Sends log -----

class AirdropSendOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    to_address: str
    token_id: int
    token_symbol: Optional[str] = None
    network: Optional[str] = None
    wallet_id: int
    wallet_address: Optional[str] = None
    amount: Decimal
    tx_hash: Optional[str] = None
    status: str
    attempts: int
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AirdropSendListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AirdropSendOut]
