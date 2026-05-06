"""Distribution service: campaigns, recipients, sending.

Pure DB / orchestration logic. The actual transaction polling loop lives in
`distribution_worker.py`; this module exposes the helpers it (and the API
router) call into.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from eth_account import Account
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import async_session_factory
from backend.db_models import (
    AirdropToken,
    AirdropTransaction,
    DistributionCampaign,
    DistributionRecipient,
    DistributionTransaction,
    DistributionWallet,
)
from backend.services.crypto import decrypt_private_key, encrypt_private_key

logger = logging.getLogger(__name__)


# ---------------- Wallets ----------------

def derive_address(private_key_hex: str) -> str:
    """Return the lowercase 0x address for a hex private key."""
    pk = private_key_hex.strip()
    if not pk.startswith("0x"):
        pk = "0x" + pk
    acct = Account.from_key(pk)
    return acct.address.lower()


async def add_wallet(session: AsyncSession, *, private_key: str, label: Optional[str]) -> DistributionWallet:
    address = derive_address(private_key)
    ciphertext, nonce = encrypt_private_key(private_key)
    wallet = DistributionWallet(
        address=address,
        label=label,
        encrypted_private_key=ciphertext,
        key_nonce=nonce,
        is_active=True,
    )
    session.add(wallet)
    await session.commit()
    await session.refresh(wallet)
    return wallet


async def list_wallets(session: AsyncSession) -> list[DistributionWallet]:
    rows = await session.scalars(select(DistributionWallet).order_by(DistributionWallet.id))
    return list(rows.all())


async def get_wallet_decrypted_key(session: AsyncSession, wallet_id: int) -> str:
    w = await session.get(DistributionWallet, wallet_id)
    if w is None:
        raise ValueError(f"Wallet {wallet_id} not found")
    return decrypt_private_key(w.encrypted_private_key, w.key_nonce)


# ---------------- Campaigns ----------------

async def create_campaign(
    session: AsyncSession,
    *,
    name: str,
    token_id: int,
    amount_per_recipient: Decimal,
    recipient_filter: dict[str, Any],
    max_total_amount: Optional[Decimal],
    dry_run: bool,
) -> DistributionCampaign:
    token = await session.get(AirdropToken, token_id)
    if token is None:
        raise ValueError(f"Token {token_id} not found")

    campaign = DistributionCampaign(
        name=name,
        token_id=token_id,
        amount_per_recipient=amount_per_recipient,
        network=token.network,
        status="draft",
        dry_run=dry_run,
        recipient_filter=recipient_filter or {},
        max_total_amount=max_total_amount,
    )
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return campaign


async def get_campaign(session: AsyncSession, campaign_id: int) -> DistributionCampaign | None:
    return await session.get(DistributionCampaign, campaign_id)


async def list_campaigns(session: AsyncSession) -> list[DistributionCampaign]:
    rows = await session.scalars(
        select(DistributionCampaign).order_by(DistributionCampaign.created_at.desc())
    )
    return list(rows.all())


async def campaign_counts(session: AsyncSession, campaign_id: int) -> dict[str, int]:
    """Return recipient counts grouped by status for a campaign."""
    stmt = (
        select(DistributionRecipient.status, func.count())
        .where(DistributionRecipient.campaign_id == campaign_id)
        .group_by(DistributionRecipient.status)
    )
    rows = (await session.execute(stmt)).all()
    out = {"pending": 0, "sending": 0, "sent": 0, "confirmed": 0, "failed": 0, "skipped": 0}
    for status_, count in rows:
        out[str(status_)] = int(count)
    out["total"] = sum(out.values())
    return out


async def build_recipients(session: AsyncSession, campaign_id: int) -> tuple[int, int]:
    """Populate `distribution_recipients` for a campaign from `airdrop_transactions`.

    Idempotent — safe to call multiple times.
    Returns ``(inserted, total_recipients)``.
    """
    campaign = await session.get(DistributionCampaign, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")

    f = campaign.recipient_filter or {}

    # Build the deduplicated recipient query against airdrop_transactions.
    conditions = []
    if f.get("token_symbol"):
        token_id = await session.scalar(
            select(AirdropToken.id).where(AirdropToken.symbol == str(f["token_symbol"]).strip().upper())
        )
        if token_id is None:
            return 0, await _count_recipients(session, campaign_id)
        conditions.append(AirdropTransaction.token_id == token_id)

    def _parse_date(s: str, *, end_of_day: bool) -> datetime:
        d = datetime.strptime(s, "%Y-%m-%d")
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59)
        return d.replace(tzinfo=timezone.utc)

    if f.get("from_date"):
        conditions.append(AirdropTransaction.transferred_at >= _parse_date(f["from_date"], end_of_day=False))
    if f.get("to_date"):
        conditions.append(AirdropTransaction.transferred_at <= _parse_date(f["to_date"], end_of_day=True))
    if f.get("min_amount_usd") is not None:
        conditions.append(AirdropTransaction.amount_usd >= Decimal(str(f["min_amount_usd"])))

    excludes = {a.lower() for a in (f.get("exclude_addresses") or [])}

    where = and_(*conditions) if conditions else None

    addr_stmt = select(AirdropTransaction.to_address).distinct()
    if where is not None:
        addr_stmt = addr_stmt.where(where)
    if f.get("limit"):
        addr_stmt = addr_stmt.limit(int(f["limit"]))

    rows = (await session.execute(addr_stmt)).all()
    addresses = [str(r[0]).lower() for r in rows if str(r[0]).lower() not in excludes]
    if not addresses:
        return 0, await _count_recipients(session, campaign_id)

    # ON CONFLICT DO NOTHING — idempotent.
    BATCH = 1000
    inserted = 0
    amount = campaign.amount_per_recipient
    for i in range(0, len(addresses), BATCH):
        chunk = addresses[i : i + BATCH]
        stmt = pg_insert(DistributionRecipient).values(
            [{"campaign_id": campaign_id, "address": a, "amount": amount} for a in chunk]
        )
        stmt = stmt.on_conflict_do_nothing(constraint="uq_dist_recipient_campaign_address")
        result = await session.execute(stmt)
        inserted += result.rowcount or 0
    await session.commit()

    if campaign.status == "draft" and (await _count_recipients(session, campaign_id)) > 0:
        campaign.status = "ready"
        await session.commit()

    return inserted, await _count_recipients(session, campaign_id)


async def _count_recipients(session: AsyncSession, campaign_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(DistributionRecipient).where(
                DistributionRecipient.campaign_id == campaign_id
            )
        )
        or 0
    )


async def transition_campaign(session: AsyncSession, campaign_id: int, new_status: str) -> DistributionCampaign:
    campaign = await session.get(DistributionCampaign, campaign_id)
    if campaign is None:
        raise ValueError(f"Campaign {campaign_id} not found")
    campaign.status = new_status
    if new_status == "running" and campaign.started_at is None:
        campaign.started_at = datetime.now(tz=timezone.utc)
    if new_status in ("completed", "failed"):
        campaign.finished_at = datetime.now(tz=timezone.utc)
    await session.commit()
    await session.refresh(campaign)
    return campaign


async def reset_failed_recipients(session: AsyncSession, campaign_id: int) -> int:
    """Move all `failed` recipients back to `pending`. Returns rows updated."""
    from sqlalchemy import update

    stmt = (
        update(DistributionRecipient)
        .where(
            DistributionRecipient.campaign_id == campaign_id,
            DistributionRecipient.status == "failed",
        )
        .values(status="pending", last_error=None)
    )
    result = await session.execute(stmt)
    await session.commit()
    return int(result.rowcount or 0)


# ---------------- Recipients ----------------

async def list_recipients(
    session: AsyncSession,
    campaign_id: int,
    *,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[tuple[DistributionRecipient, Optional[str]]]]:
    where = [DistributionRecipient.campaign_id == campaign_id]
    if status:
        where.append(DistributionRecipient.status == status)

    total = int(
        await session.scalar(
            select(func.count()).select_from(DistributionRecipient).where(and_(*where))
        )
        or 0
    )

    # Subquery: latest tx hash per recipient.
    latest_tx = (
        select(
            DistributionTransaction.recipient_id,
            func.max(DistributionTransaction.id).label("max_id"),
        )
        .group_by(DistributionTransaction.recipient_id)
        .subquery()
    )

    rows_stmt = (
        select(DistributionRecipient, DistributionTransaction.tx_hash)
        .outerjoin(latest_tx, latest_tx.c.recipient_id == DistributionRecipient.id)
        .outerjoin(DistributionTransaction, DistributionTransaction.id == latest_tx.c.max_id)
        .where(and_(*where))
        .order_by(DistributionRecipient.id)
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(rows_stmt)).all()
    return total, [(r[0], r[1]) for r in rows]


# ---------------- Helpers used by the worker ----------------

async def session_scope() -> AsyncSession:
    """Return a fresh AsyncSession (caller is responsible for closing)."""
    return async_session_factory()
