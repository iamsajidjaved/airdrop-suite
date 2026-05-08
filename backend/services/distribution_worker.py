"""Distribution worker — sends ERC-20 transfers from active sender wallets.

Three background tasks run concurrently when started:

* ``_send_loop``  — each tick enqueues new airdrop_sends rows from scanner data,
                    then claims and broadcasts pending sends (one per wallet, in
                    parallel across all active wallets).
* ``_watch_loop`` — polls receipts for outstanding ``broadcast`` rows and
                    marks them ``confirmed`` or ``failed``.

Per-wallet nonce safety: an in-process ``asyncio.Lock`` per wallet_id serialises
nonce use so concurrent wallet tasks don't collide on the same nonce stream.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from eth_account import Account
from sqlalchemy import and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import chain_id_for, settings
from backend.db import async_session_factory
from backend.db_models import (
    AirdropConfig,
    AirdropSend,
    AirdropToken,
    DistributionWallet,
)
from backend.services.crypto import decrypt_private_key
from backend.services.web3_client import (
    Web3Unavailable,
    build_transfer_tx,
    estimate_fees,
    get_revert_reason,
    get_token_balance,
    get_web3,
    to_checksum,
    token_units,
)

logger = logging.getLogger(__name__)


@dataclass
class WorkerState:
    enabled: bool = False
    running: bool = False
    interval_seconds: int = 10
    mode: str = "all"
    last_tick_at: Optional[str] = None
    last_error: Optional[str] = None
    in_flight: int = 0
    sent_total: int = 0
    confirmed_total: int = 0
    failed_total: int = 0
    pending_sends: int = 0


class DistributionWorker:
    def __init__(self) -> None:
        self._send_task: Optional[asyncio.Task] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._wallet_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._state = WorkerState(enabled=settings.distribution_worker_enabled)

    @property
    def state(self) -> WorkerState:
        return self._state

    # ---------------- lifecycle ----------------

    def start(self) -> None:
        if self._send_task and not self._send_task.done():
            return
        self._stop_event = asyncio.Event()
        self._state.enabled = True
        self._state.running = True
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
                await self._enqueue_tick()
                await self._send_tick()
            except Exception as e:  # noqa: BLE001
                self._state.last_error = f"tick: {type(e).__name__}: {e}"
                logger.exception("send loop tick failed: %s", e)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(1, self._state.interval_seconds),
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

    # ---------------- enqueue tick ----------------

    async def _enqueue_tick(self) -> None:
        """Insert pending airdrop_sends rows for unprocessed scanner addresses."""
        async with async_session_factory() as session:
            cfg = await self._load_config(session)

        token_id_raw = cfg.get("distribution_token_id", "")
        if not token_id_raw:
            return  # No token configured yet — nothing to enqueue.

        try:
            token_id = int(token_id_raw)
            amount = Decimal(cfg.get("distribution_amount") or "1.0")
        except (ValueError, TypeError, InvalidOperation) as e:
            logger.warning("Invalid distribution config: %s", e)
            return

        async with async_session_factory() as session:
            # Clean up pending sends whose wallet was deactivated so they can
            # be re-assigned to an active wallet on this tick.
            await session.execute(
                text(
                    """
                    DELETE FROM airdrop_sends
                    WHERE status = 'pending'
                      AND wallet_id NOT IN (
                          SELECT id FROM distribution_wallets WHERE is_active = true
                      )
                    """
                )
            )
            await session.commit()

        async with async_session_factory() as session:
            if self._state.mode == "random":
                # One randomly-chosen active wallet per address.
                # Exclude addresses that already have a non-failed send (in-progress
                # or confirmed) or that have exhausted all retries (failed send count
                # >= max_retries), to prevent infinite re-enqueue loops.
                await session.execute(
                    text(
                        """
                        INSERT INTO airdrop_sends
                            (to_address, token_id, wallet_id, amount, status)
                        SELECT DISTINCT ON (at.to_address)
                            at.to_address, :token_id, dw.id, :amount, 'pending'
                        FROM airdrop_transactions at
                        CROSS JOIN distribution_wallets dw
                        WHERE dw.is_active = true
                          AND NOT EXISTS (
                              SELECT 1 FROM airdrop_sends s
                              WHERE s.to_address = at.to_address
                                AND s.status != 'failed'
                          )
                          AND (
                              SELECT COUNT(*) FROM airdrop_sends s
                              WHERE s.to_address = at.to_address
                                AND s.status = 'failed'
                          ) < :max_retries
                        ORDER BY at.to_address, random()
                        LIMIT 50
                        ON CONFLICT ON CONSTRAINT uq_airdrop_sends_address_wallet
                        DO NOTHING
                        """
                    ),
                    {"token_id": token_id, "amount": str(amount), "max_retries": int(cfg.get("distribution_max_retries", "3"))},
                )
            else:
                # All active wallets each send to every address.
                await session.execute(
                    text(
                        """
                        INSERT INTO airdrop_sends
                            (to_address, token_id, wallet_id, amount, status)
                        SELECT DISTINCT at.to_address, :token_id, dw.id, :amount, 'pending'
                        FROM airdrop_transactions at
                        CROSS JOIN distribution_wallets dw
                        WHERE dw.is_active = true
                          AND NOT EXISTS (
                              SELECT 1 FROM airdrop_sends s
                              WHERE s.to_address = at.to_address
                                AND s.wallet_id = dw.id
                                AND s.status != 'failed'
                          )
                          AND (
                              SELECT COUNT(*) FROM airdrop_sends s
                              WHERE s.to_address = at.to_address
                                AND s.wallet_id = dw.id
                                AND s.status = 'failed'
                          ) < :max_retries
                        LIMIT 50
                        ON CONFLICT ON CONSTRAINT uq_airdrop_sends_address_wallet
                        DO NOTHING
                        """
                    ),
                    {"token_id": token_id, "amount": str(amount), "max_retries": int(cfg.get("distribution_max_retries", "3"))},
                )
            await session.commit()

    # ---------------- send tick ----------------

    async def _send_tick(self) -> None:
        """Claim and broadcast one pending send per active wallet (parallel)."""
        async with async_session_factory() as session:
            wallets = list((await session.scalars(
                select(DistributionWallet)
                .where(DistributionWallet.is_active.is_(True))
                .order_by(DistributionWallet.id)
            )).all())

        if not wallets:
            return

        tasks = [
            asyncio.create_task(self._process_one_wallet(w.id))
            for w in wallets
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Update pending count for the status display.
        async with async_session_factory() as session:
            self._state.pending_sends = int(
                await session.scalar(
                    select(func.count()).select_from(AirdropSend).where(AirdropSend.status == "pending")
                ) or 0
            )

    async def _process_one_wallet(self, wallet_id: int) -> None:
        """Claim and send one pending send for a specific wallet."""
        async with self._wallet_locks[wallet_id]:
            self._state.in_flight += 1
            try:
                async with async_session_factory() as session:
                    send = await self._claim_send(session, wallet_id)
                    if send is None:
                        return
                    send_id = send.id
                try:
                    async with async_session_factory() as session:
                        await self._send_transfer(session, send_id, wallet_id)
                except Exception as e:  # noqa: BLE001
                    err_msg = str(e) or type(e).__name__
                    async with async_session_factory() as session:
                        await self._mark_send_failed(session, send_id, err_msg)
                    self._state.last_error = f"send: {type(e).__name__}: {e}"
                    logger.exception("send failed for send_id=%s wallet=%s: %s", send_id, wallet_id, e)
            finally:
                self._state.in_flight = max(0, self._state.in_flight - 1)

    async def _claim_send(
        self, session: AsyncSession, wallet_id: int
    ) -> Optional[AirdropSend]:
        """Atomically claim one pending send for the given wallet.

        Uses a raw UPDATE…RETURNING so the inner FOR UPDATE SKIP LOCKED only
        locks airdrop_sends rows — not the joined token/wallet tables, which
        the ORM's lazy="joined" would otherwise pull in and cause every row to
        be skipped under concurrent access.
        """
        result = await session.execute(
            text("""
                UPDATE airdrop_sends
                SET status = 'broadcast', attempts = attempts + 1
                WHERE id = (
                    SELECT id FROM airdrop_sends
                    WHERE status = 'pending' AND wallet_id = :wallet_id
                    ORDER BY id
                    LIMIT 1
                    FOR UPDATE OF airdrop_sends SKIP LOCKED
                )
                RETURNING id
            """),
            {"wallet_id": wallet_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        await session.commit()
        return await session.get(AirdropSend, row[0])

    async def _send_transfer(
        self, session: AsyncSession, send_id: int, wallet_id: int
    ) -> None:
        send = await session.get(AirdropSend, send_id)
        if send is None:
            raise RuntimeError(f"Send row {send_id} disappeared")

        try:
            w3 = get_web3()
        except Web3Unavailable as e:
            raise RuntimeError(str(e)) from e

        wallet = await session.get(DistributionWallet, wallet_id)
        token = await session.get(AirdropToken, send.token_id)
        if wallet is None:
            raise RuntimeError(f"Wallet {wallet_id} not found")
        if token is None:
            raise RuntimeError(f"Token {send.token_id} not found")

        private_key = decrypt_private_key(wallet.encrypted_private_key, wallet.key_nonce)
        account = Account.from_key(private_key)

        chain_id = await w3.eth.chain_id
        expected_chain_id = chain_id_for(token.network)
        if chain_id != expected_chain_id:
            raise RuntimeError(
                f"Chain ID mismatch: token '{token.symbol}' is for network "
                f"'{token.network}' (chain {expected_chain_id}), RPC returned chain {chain_id}."
            )

        nonce = await w3.eth.get_transaction_count(to_checksum(wallet.address), "pending")
        max_fee, tip = await estimate_fees()

        eth_balance_wei = await w3.eth.get_balance(to_checksum(wallet.address))
        estimated_gas_cost_wei = max_fee * 150_000
        if eth_balance_wei < estimated_gas_cost_wei:
            eth_have = Decimal(eth_balance_wei) / Decimal(10**18)
            eth_need = Decimal(estimated_gas_cost_wei) / Decimal(10**18)
            raise RuntimeError(
                f"Insufficient ETH for gas: {wallet.address} has {eth_have:.6f} ETH, "
                f"need ~{eth_need:.6f} ETH"
            )

        token_balance = await get_token_balance(token.contract_address, wallet.address, token.decimals)
        if token_balance < Decimal(str(send.amount)):
            raise RuntimeError(
                f"Insufficient {token.symbol}: {wallet.address} has {token_balance}, "
                f"needs {send.amount}"
            )

        amount_units = token_units(Decimal(send.amount), token.decimals)
        tx = await build_transfer_tx(
            token_address=token.contract_address,
            sender=wallet.address,
            recipient=send.to_address,
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

        send.tx_hash = tx_hash_hex
        send.last_error = None
        await session.commit()
        self._state.sent_total += 1
        logger.info(
            "sent %s -> %s (%s %s) tx=%s",
            wallet.address, send.to_address, send.amount, token.symbol, tx_hash_hex,
        )

    async def _mark_send_failed(
        self, session: AsyncSession, send_id: int, err: str
    ) -> None:
        send = await session.get(AirdropSend, send_id)
        if send is None:
            return
        cfg_row = await session.get(AirdropConfig, "distribution_max_retries")
        max_retries = int((cfg_row.value if cfg_row else None) or 3)
        send.status = "failed" if send.attempts >= max_retries else "pending"
        send.last_error = err[:2000]
        await session.commit()
        if send.status == "failed":
            self._state.failed_total += 1

    # ---------------- watch tick ----------------

    async def _watch_tick(self) -> None:
        try:
            w3 = get_web3()
        except Web3Unavailable:
            return

        async with async_session_factory() as session:
            sends = list((await session.scalars(
                select(AirdropSend)
                .where(AirdropSend.status == "broadcast")
                .limit(50)
            )).all())
            if not sends:
                return

            for send in sends:
                try:
                    receipt = await w3.eth.get_transaction_receipt(send.tx_hash)
                except Exception:
                    receipt = None
                if receipt is None:
                    continue
                ok = int(receipt.get("status") or 0) == 1
                if ok:
                    send.status = "confirmed"
                    self._state.confirmed_total += 1
                else:
                    revert_detail = await get_revert_reason(send.tx_hash, w3)
                    send.status = "failed"
                    send.last_error = (f"tx reverted: {revert_detail}"[:2000]
                                      if revert_detail else "tx reverted")
                    self._state.failed_total += 1
            await session.commit()

    # ---------------- helpers ----------------

    async def _load_config(self, session: AsyncSession) -> dict[str, str]:
        """Load distribution config from airdrop_config and update worker state."""
        rows = list((await session.scalars(
            select(AirdropConfig).where(AirdropConfig.key.like("distribution_%"))
        )).all())
        cfg = {r.key: r.value for r in rows}
        self._state.interval_seconds = int(cfg.get("distribution_interval_seconds", "10"))
        self._state.mode = cfg.get("distribution_mode", "all")
        return cfg


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# Module-level singleton.
worker = DistributionWorker()
