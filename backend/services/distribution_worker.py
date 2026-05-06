"""Distribution worker — sends ERC-20 transfers from sender wallets.

Two background tasks run concurrently when started:

* ``_send_loop``  — for each running campaign, claim a `pending` recipient,
                    pick a free sender wallet, sign and broadcast a transfer,
                    insert a `distribution_transactions` row.
* ``_watch_loop`` — poll receipts for outstanding `broadcast` rows; mark
                    `success` / `reverted` / `dropped` and update recipient
                    status accordingly.

Per-wallet nonce safety is provided by an in-process `asyncio.Lock` plus a
reconciliation against `eth_getTransactionCount(pending)` on every send.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from eth_account import Account
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db import async_session_factory
from backend.db_models import (
    AirdropToken,
    DistributionCampaign,
    DistributionRecipient,
    DistributionTransaction,
    DistributionWallet,
)
from backend.services.crypto import decrypt_private_key
from backend.services.web3_client import (
    Web3Unavailable,
    build_transfer_tx,
    estimate_fees,
    get_web3,
    to_checksum,
    token_units,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkerState:
    enabled: bool = False
    running: bool = False
    interval_seconds: int = 0
    last_tick_at: Optional[str] = None
    last_error: Optional[str] = None
    in_flight: int = 0
    sent_total: int = 0
    confirmed_total: int = 0
    failed_total: int = 0


class DistributionWorker:
    def __init__(self) -> None:
        self._send_task: Optional[asyncio.Task] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._wallet_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._inflight_sem: Optional[asyncio.Semaphore] = None
        self._state = WorkerState(
            enabled=settings.distribution_worker_enabled,
            interval_seconds=settings.distribution_worker_interval_seconds,
        )

    @property
    def state(self) -> WorkerState:
        return self._state

    # ---------------- lifecycle ----------------

    def start(self) -> None:
        if self._send_task and not self._send_task.done():
            return
        self._stop_event = asyncio.Event()
        self._inflight_sem = asyncio.Semaphore(max(1, settings.distribution_max_inflight))
        self._state.enabled = True
        self._state.running = True
        self._state.interval_seconds = settings.distribution_worker_interval_seconds
        self._send_task = asyncio.create_task(self._send_loop(), name="dist-send-loop")
        self._watch_task = asyncio.create_task(self._watch_loop(), name="dist-watch-loop")
        logger.info("Distribution worker started.")

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        for t in (self._send_task, self._watch_task):
            if t is not None:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._send_task = None
        self._watch_task = None
        self._stop_event = None
        self._state.running = False
        logger.info("Distribution worker stopped.")

    # ---------------- main loops ----------------

    async def _send_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            self._state.last_tick_at = _iso_now()
            try:
                await self._send_tick()
            except Exception as e:  # noqa: BLE001
                self._state.last_error = f"send_tick: {type(e).__name__}: {e}"
                logger.exception("send_tick failed: %s", e)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(1, settings.distribution_worker_interval_seconds),
                )
                return
            except asyncio.TimeoutError:
                continue

    async def _watch_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self._watch_tick()
            except Exception as e:  # noqa: BLE001
                self._state.last_error = f"watch_tick: {type(e).__name__}: {e}"
                logger.exception("watch_tick failed: %s", e)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(2, settings.distribution_receipt_poll_seconds),
                )
                return
            except asyncio.TimeoutError:
                continue

    # ---------------- send tick ----------------

    async def _send_tick(self) -> None:
        from backend.services.distribution_service import get_effective_campaign_wallets

        per_campaign_wallets: list[tuple[int, list[int]]] = []
        async with async_session_factory() as session:
            # Pick all running, non-dry-run campaigns.
            campaigns = list((await session.scalars(
                select(DistributionCampaign).where(
                    DistributionCampaign.status == "running",
                    DistributionCampaign.dry_run.is_(False),
                )
            )).all())
            if not campaigns:
                return

            had_any = False
            for c in campaigns:
                wallets = await get_effective_campaign_wallets(session, c)
                if wallets:
                    had_any = True
                    per_campaign_wallets.append((c.id, [w.id for w in wallets]))
                else:
                    logger.warning("campaign %s has no eligible sender wallets", c.id)

            if not had_any:
                self._state.last_error = "No active sender wallets configured for any running campaign."
                return

        # Round-robin across (campaign, wallet) until inflight cap reached or no work.
        tasks: list[asyncio.Task] = []
        for campaign_id, wallet_ids in per_campaign_wallets:
            for wallet_id in wallet_ids:
                if self._inflight_sem is None or self._inflight_sem.locked():
                    break
                tasks.append(asyncio.create_task(self._process_one(campaign_id, wallet_id)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_one(self, campaign_id: int, wallet_id: int) -> None:
        assert self._inflight_sem is not None
        async with self._inflight_sem:
            self._state.in_flight += 1
            try:
                async with async_session_factory() as session:
                    recipient = await self._claim_recipient(session, campaign_id)
                    if recipient is None:
                        return
                    try:
                        async with self._wallet_locks[wallet_id]:
                            await self._send_to_recipient(session, recipient, wallet_id)
                    except Exception as e:  # noqa: BLE001
                        await self._mark_recipient_failed(session, recipient.id, str(e))
                        self._state.last_error = f"send: {type(e).__name__}: {e}"
                        logger.exception("send failed for recipient %s: %s", recipient.id, e)
            finally:
                self._state.in_flight = max(0, self._state.in_flight - 1)

    async def _claim_recipient(
        self, session: AsyncSession, campaign_id: int
    ) -> Optional[DistributionRecipient]:
        """SELECT … FOR UPDATE SKIP LOCKED — atomically claim one pending recipient."""
        stmt = (
            select(DistributionRecipient)
            .where(
                DistributionRecipient.campaign_id == campaign_id,
                DistributionRecipient.status == "pending",
            )
            .order_by(DistributionRecipient.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        recipient = (await session.execute(stmt)).scalar_one_or_none()
        if recipient is None:
            return None
        recipient.status = "sending"
        recipient.attempts += 1
        await session.commit()
        await session.refresh(recipient)
        return recipient

    async def _send_to_recipient(
        self,
        session: AsyncSession,
        recipient: DistributionRecipient,
        wallet_id: int,
    ) -> None:
        try:
            w3 = get_web3()
        except Web3Unavailable as e:
            raise RuntimeError(str(e)) from e

        wallet = await session.get(DistributionWallet, wallet_id)
        campaign = await session.get(DistributionCampaign, recipient.campaign_id)
        if wallet is None or campaign is None:
            raise RuntimeError("wallet or campaign disappeared")
        token = await session.get(AirdropToken, campaign.token_id)
        if token is None:
            raise RuntimeError("token row missing")

        # Decrypt key just in time; never store plaintext.
        private_key = decrypt_private_key(wallet.encrypted_private_key, wallet.key_nonce)
        account = Account.from_key(private_key)

        chain_id = await w3.eth.chain_id
        nonce = await w3.eth.get_transaction_count(to_checksum(wallet.address), "pending")
        max_fee, tip = await estimate_fees()
        amount_units = token_units(Decimal(recipient.amount), token.decimals)

        tx = await build_transfer_tx(
            token_address=token.contract_address,
            sender=wallet.address,
            recipient=recipient.address,
            amount_units=amount_units,
            nonce=nonce,
            max_fee_wei=max_fee,
            max_priority_wei=tip,
            chain_id=chain_id,
        )

        signed = account.sign_transaction(tx)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash_hex = tx_hash.hex() if isinstance(tx_hash, (bytes, bytearray)) else str(tx_hash)
        if not tx_hash_hex.startswith("0x"):
            tx_hash_hex = "0x" + tx_hash_hex

        # Persist the broadcast.
        session.add(
            DistributionTransaction(
                recipient_id=recipient.id,
                wallet_id=wallet.id,
                nonce=nonce,
                tx_hash=tx_hash_hex,
                max_fee_wei=Decimal(max_fee),
                max_priority_wei=Decimal(tip),
                status="broadcast",
            )
        )
        recipient.status = "sent"
        recipient.assigned_wallet_id = wallet.id
        recipient.last_error = None
        await session.commit()
        self._state.sent_total += 1
        logger.info("sent %s -> %s (%s) tx=%s", wallet.address, recipient.address, token.symbol, tx_hash_hex)

    async def _mark_recipient_failed(self, session: AsyncSession, recipient_id: int, err: str) -> None:
        try:
            r = await session.get(DistributionRecipient, recipient_id)
            if r is None:
                return
            max_retries = max(1, settings.distribution_max_retries_per_recipient)
            new_status = "failed" if r.attempts >= max_retries else "pending"
            r.status = new_status
            r.last_error = err[:2000]
            await session.commit()
            if new_status == "failed":
                self._state.failed_total += 1
        except Exception:  # noqa: BLE001
            logger.exception("failed to mark recipient %s as failed", recipient_id)

    # ---------------- watch tick ----------------

    async def _watch_tick(self) -> None:
        try:
            w3 = get_web3()
        except Web3Unavailable:
            return

        async with async_session_factory() as session:
            rows = list((await session.scalars(
                select(DistributionTransaction).where(DistributionTransaction.status == "broadcast").limit(50)
            )).all())
            if not rows:
                return

            for tx in rows:
                try:
                    receipt = await w3.eth.get_transaction_receipt(tx.tx_hash)
                except Exception:
                    receipt = None
                if receipt is None:
                    continue
                tx.block_number = int(receipt.get("blockNumber") or 0)
                tx.gas_used = int(receipt.get("gasUsed") or 0)
                tx.confirmed_at = datetime.now(tz=timezone.utc)
                ok = int(receipt.get("status") or 0) == 1
                tx.status = "success" if ok else "reverted"

                # Update recipient.
                recipient = await session.get(DistributionRecipient, tx.recipient_id)
                if recipient is not None:
                    if ok:
                        recipient.status = "confirmed"
                        self._state.confirmed_total += 1
                    else:
                        recipient.status = "failed"
                        recipient.last_error = "tx reverted"
                        self._state.failed_total += 1
            await session.commit()

            # Auto-complete campaigns where every recipient is in a terminal state.
            await self._maybe_complete_campaigns(session)

    async def _maybe_complete_campaigns(self, session: AsyncSession) -> None:
        from sqlalchemy import func as sa_func

        running = list((await session.scalars(
            select(DistributionCampaign).where(DistributionCampaign.status == "running")
        )).all())
        for c in running:
            pending = await session.scalar(
                select(sa_func.count()).select_from(DistributionRecipient).where(
                    and_(
                        DistributionRecipient.campaign_id == c.id,
                        DistributionRecipient.status.in_(("pending", "sending", "sent")),
                    )
                )
            )
            if pending == 0:
                c.status = "completed"
                c.finished_at = datetime.now(tz=timezone.utc)
                logger.info("campaign %s completed", c.id)
        await session.commit()


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# Module-level singleton (mirrors the Phase 1 scheduler pattern).
worker = DistributionWorker()
