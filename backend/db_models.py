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
    Numeric,
    String,
    UniqueConstraint,
    func,
)
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
