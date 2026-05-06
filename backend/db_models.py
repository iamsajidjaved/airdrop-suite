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
    network: Mapped[str] = mapped_column(String(32), nullable=False, default="ethereum", server_default="ethereum")
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    token: Mapped[AirdropToken] = relationship(back_populates="transactions")

    __table_args__ = (
        UniqueConstraint("tx_hash", "log_index", "token_id", name="uq_airdrop_tx_hash_log_token"),
        Index("ix_airdrop_tx_token_block", "token_id", "block_number"),
        Index("ix_airdrop_tx_to", "to_address"),
        Index("ix_airdrop_tx_from", "from_address"),
        Index("ix_airdrop_tx_transferred_at", "transferred_at"),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DistributionCampaign(Base):
    __tablename__ = "distribution_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    token_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("airdrop_tokens.id", ondelete="RESTRICT"), nullable=False
    )
    amount_per_recipient: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    network: Mapped[str] = mapped_column(String(32), nullable=False, default="ethereum", server_default="ethereum")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", server_default="draft")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    recipient_filter: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    max_total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(38, 18), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    token: Mapped[AirdropToken] = relationship(lazy="joined")
    recipients: Mapped[list["DistributionRecipient"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (Index("ix_dist_campaign_status", "status"),)


class DistributionRecipient(Base):
    __tablename__ = "distribution_recipients"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("distribution_campaigns.id", ondelete="CASCADE"), nullable=False
    )
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", server_default="pending")
    assigned_wallet_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("distribution_wallets.id", ondelete="SET NULL"), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    campaign: Mapped[DistributionCampaign] = relationship(back_populates="recipients")

    __table_args__ = (
        UniqueConstraint("campaign_id", "address", name="uq_dist_recipient_campaign_address"),
        Index("ix_dist_recipient_campaign_status", "campaign_id", "status"),
    )


class DistributionTransaction(Base):
    __tablename__ = "distribution_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    recipient_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("distribution_recipients.id", ondelete="CASCADE"), nullable=False
    )
    wallet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("distribution_wallets.id", ondelete="RESTRICT"), nullable=False
    )
    nonce: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tx_hash: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, unique=True)
    max_fee_wei: Mapped[Optional[Decimal]] = mapped_column(Numeric(38, 0), nullable=True)
    max_priority_wei: Mapped[Optional[Decimal]] = mapped_column(Numeric(38, 0), nullable=True)
    gas_used: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    block_number: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="broadcast", server_default="broadcast")
    broadcast_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_dist_tx_recipient", "recipient_id"),
        Index("ix_dist_tx_status", "status"),
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
