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


class AirdropRecipient(BaseModel):
    address: str
    first_seen_tx: str
    first_seen_token: str
    first_seen_contract: str
    first_seen_amount: float
    first_seen_block: int
    first_seen_datetime_utc: str


class MonitorRunResult(BaseModel):
    tokens_scanned: list[str]
    new_transfers_inserted: int
    total_transfers_stored: int
    blocks_scanned_per_token: dict[str, dict]
    run_timestamp: str
    errors: list[str]


class AirdropStatusResponse(BaseModel):
    last_run_timestamp: Optional[str]
    last_block_per_token: dict[str, int]
    total_transfers: int


# ----- Token CRUD -----

class AirdropTokenBase(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    contract_address: str = Field(..., min_length=42, max_length=64)
    decimals: int = Field(..., ge=0, le=36)
    network: str = Field(default="ethereum", max_length=32)
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
    network: Optional[str] = Field(None, max_length=32)
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


class AirdropConfigUpdate(BaseModel):
    min_threshold_usd: float = Field(..., gt=0)


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
    created_at: datetime


class AirdropTransactionListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[AirdropTransactionOut]
