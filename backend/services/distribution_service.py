"""Distribution service: sender wallet management and airdrop send tracking.

The actual transaction broadcasting loop lives in distribution_worker.py;
this module exposes the helpers it (and the API router) call into.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from eth_account import Account
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db_models import (
    AirdropConfig,
    AirdropSend,
    AirdropToken,
    DistributionWallet,
)
from backend.services.crypto import decrypt_private_key, encrypt_private_key
from backend.services.web3_client import (
    Web3Unavailable,
    get_eth_balance,
    get_token_balance,
    get_web3,
)

logger = logging.getLogger(__name__)

_BALANCE_RPC_TIMEOUT = 20.0


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
    await refresh_wallet_balances(session, wallet)
    return wallet


async def list_wallets(session: AsyncSession) -> list[DistributionWallet]:
    rows = await session.scalars(select(DistributionWallet).order_by(DistributionWallet.id))
    return list(rows.all())


async def refresh_wallet_balances(
    session: AsyncSession, wallet: DistributionWallet
) -> DistributionWallet:
    """Fetch live ETH + active-token balances and persist them on the wallet row.

    All RPC calls run in parallel. Per-call failures are logged and skipped so
    a single broken token contract never poisons the whole refresh.

    If the RPC is unconfigured, the wallet is left untouched (no exception).
    """
    try:
        get_web3()
    except Web3Unavailable:
        logger.info("Skipping balance refresh for %s: ETH_RPC_URL not configured", wallet.address)
        return wallet

    tokens = list(
        (await session.scalars(
            select(AirdropToken).where(AirdropToken.is_active.is_(True))
        )).all()
    )

    async def _eth() -> Optional[Decimal]:
        try:
            return await asyncio.wait_for(
                get_eth_balance(wallet.address), timeout=_BALANCE_RPC_TIMEOUT
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("eth balance failed for %s: %s: %r", wallet.address, type(e).__name__, e)
            return None

    async def _tok(token: AirdropToken) -> Optional[Decimal]:
        try:
            return await asyncio.wait_for(
                get_token_balance(token.contract_address, wallet.address, token.decimals),
                timeout=_BALANCE_RPC_TIMEOUT,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "token balance failed for %s/%s: %s: %r",
                wallet.address, token.symbol, type(e).__name__, e,
            )
            return None

    eth_res, *tok_res = await asyncio.gather(_eth(), *(_tok(t) for t in tokens))

    wallet.eth_balance = eth_res
    wallet.token_balances = {
        t.symbol: str(bal) for t, bal in zip(tokens, tok_res) if bal is not None
    }
    wallet.balances_updated_at = datetime.now(tz=timezone.utc)
    await session.commit()
    await session.refresh(wallet)
    return wallet


async def get_wallet_decrypted_key(session: AsyncSession, wallet_id: int) -> str:
    w = await session.get(DistributionWallet, wallet_id)
    if w is None:
        raise ValueError(f"Wallet {wallet_id} not found")
    return decrypt_private_key(w.encrypted_private_key, w.key_nonce)


# ---------------- Distribution config ----------------

async def get_distribution_config(session: AsyncSession) -> dict[str, str]:
    """Return all distribution_* keys from airdrop_config as a plain dict."""
    rows = list(
        (await session.scalars(
            select(AirdropConfig).where(AirdropConfig.key.like("distribution_%"))
        )).all()
    )
    return {r.key: r.value for r in rows}


async def set_distribution_config(session: AsyncSession, updates: dict[str, str]) -> None:
    """Upsert distribution config keys using ON CONFLICT DO UPDATE."""
    for key, value in updates.items():
        stmt = (
            pg_insert(AirdropConfig)
            .values(key=key, value=value)
            .on_conflict_do_update(index_elements=["key"], set_={"value": value})
        )
        await session.execute(stmt)
    await session.commit()


# ---------------- Sends ----------------

async def list_sends(
    session: AsyncSession,
    *,
    status: Optional[str] = None,
    to_address: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[int, list[AirdropSend]]:
    where = []
    if status:
        where.append(AirdropSend.status == status)
    if to_address:
        where.append(AirdropSend.to_address == to_address.lower())

    count_stmt = select(func.count()).select_from(AirdropSend)
    rows_stmt = (
        select(AirdropSend)
        .order_by(AirdropSend.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if where:
        condition = and_(*where)
        count_stmt = count_stmt.where(condition)
        rows_stmt = rows_stmt.where(condition)

    total = int(await session.scalar(count_stmt) or 0)
    rows = list((await session.scalars(rows_stmt)).all())
    return total, rows


