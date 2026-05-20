"""SQLAlchemy ORM models for the airdrop feature."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class AirdropToken(Base):
    __tablename__ = "airdrop_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    contract_address: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    # network column removed — global active_network config key in airdrop_config now controls this
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_scanned_block: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    transactions: Mapped[list["AirdropTransaction"]] = relationship(
        back_populates="token", cascade="all, delete-orphan", passive_deletes=True
    )


class AirdropConfig(Base):
    __tablename__ = "airdrop_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IgamingBrand(Base):
    """An iGaming competitor brand whose outgoing ERC-20 transfers we scan to identify their users."""

    __tablename__ = "igaming_brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    wallet_address: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_scanned_block: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    transactions: Mapped[list["AirdropTransaction"]] = relationship(
        back_populates="brand", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("wallet_address", name="uq_igaming_brands_wallet"),
        Index("ix_igaming_brands_wallet_address", "wallet_address"),
    )


class AirdropTransaction(Base):
    __tablename__ = "airdrop_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tx_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    log_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    network: Mapped[str] = mapped_column(String(32), nullable=False, default="ethereum", server_default="ethereum")
    token_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("airdrop_tokens.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_address: Mapped[str] = mapped_column(String(64), nullable=False)
    to_address: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    amount_usd: Mapped[Optional[Decimal]] = mapped_column(Numeric(38, 8), nullable=True)
    transferred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # scan_mode: which scanner produced this row ("standard" or "igaming")
    scan_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="standard", server_default="standard")
    # igaming_brand_id: set when scan_mode == "igaming"
    igaming_brand_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("igaming_brands.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    token: Mapped[AirdropToken] = relationship(back_populates="transactions")
    brand: Mapped[Optional[IgamingBrand]] = relationship(back_populates="transactions")

    __table_args__ = (
        UniqueConstraint("tx_hash", "log_index", "token_id", name="uq_airdrop_tx_hash_log_token"),
        Index("ix_airdrop_tx_token_block", "token_id", "block_number"),
        Index("ix_airdrop_tx_to", "to_address"),
        Index("ix_airdrop_tx_from", "from_address"),
        Index("ix_airdrop_tx_transferred_at", "transferred_at"),
        Index("ix_airdrop_tx_scan_mode", "scan_mode"),
    )


# ===================== Phase 2: Token Distribution =====================


class DistributionWallet(Base):
    __tablename__ = "distribution_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    address: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    encrypted_private_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_nonce: Mapped[bytes] = mapped_column(LargeBinary(12), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    eth_balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(38, 18), nullable=True)
    token_balances: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    balances_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AirdropSend(Base):
    __tablename__ = "airdrop_sends"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    to_address: Mapped[str] = mapped_column(String(64), nullable=False)
    token_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("airdrop_tokens.id", ondelete="CASCADE"), nullable=False
    )
    wallet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("distribution_wallets.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    tx_hash: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    token: Mapped["AirdropToken"] = relationship(lazy="joined")
    wallet: Mapped["DistributionWallet"] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint("to_address", "wallet_id", "token_id", name="uq_airdrop_sends_address_wallet_token"),
        Index("ix_airdrop_sends_to_address", "to_address"),
        Index("ix_airdrop_sends_status", "status"),
    )


# ===================== Quality Filtering =====================


class QualityAddressBlocklist(Base):
    __tablename__ = "quality_address_blocklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    network: Mapped[str] = mapped_column(String(32), nullable=False, default="ethereum", server_default="ethereum")
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual", server_default="manual")
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("network", "address", name="uq_quality_blocklist_network_address"),
        Index("ix_quality_blocklist_address", "address"),
    )


class WalletContractCache(Base):
    __tablename__ = "wallet_contract_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    network: Mapped[str] = mapped_column(String(32), nullable=False, default="ethereum", server_default="ethereum")
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    is_contract: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("network", "address", name="uq_wallet_contract_cache_network_address"),
        Index("ix_wallet_contract_cache_address", "address"),
    )
