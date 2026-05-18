"""Distribution API endpoints."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import require_admin
from backend.config import settings
from backend.db import get_session
from backend.db_models import AirdropSend, AirdropToken, DistributionWallet
from backend.models import (
    AirdropSendListResponse,
    AirdropSendOut,
    DistributionConfigOut,
    DistributionConfigUpdate,
    DistributionWalletCreate,
    DistributionWalletOut,
    DistributionWalletUpdate,
    DistributionWorkerState,
)
from backend.services import distribution_service as dist
from backend.services.crypto import CryptoUnavailable
from backend.services.distribution_worker import worker as distribution_worker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/distribution", tags=["distribution"])


# ---------------- Wallets ----------------

@router.get("/wallets", response_model=list[DistributionWalletOut])
async def list_wallets(session: AsyncSession = Depends(get_session)):
    """Return wallets with their cached balances (never hits the RPC)."""
    wallets = await dist.list_wallets(session)
    return [DistributionWalletOut.model_validate(w) for w in wallets]


@router.post(
    "/wallets",
    response_model=DistributionWalletOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_wallet(
    payload: DistributionWalletCreate,
    session: AsyncSession = Depends(get_session),
):
    try:
        wallet = await dist.add_wallet(session, private_key=payload.private_key, label=payload.label)
    except CryptoUnavailable as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Wallet already exists: {e.orig}") from e
    return DistributionWalletOut.model_validate(wallet)


@router.post(
    "/wallets/{wallet_id}/refresh",
    response_model=DistributionWalletOut,
    dependencies=[Depends(require_admin)],
)
async def refresh_wallet(wallet_id: int, session: AsyncSession = Depends(get_session)):
    """Fetch live ETH + token balances from the chain and persist them."""
    wallet = await session.get(DistributionWallet, wallet_id)
    if wallet is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    wallet = await dist.refresh_wallet_balances(session, wallet)
    return DistributionWalletOut.model_validate(wallet)


@router.patch(
    "/wallets/{wallet_id}",
    response_model=DistributionWalletOut,
    dependencies=[Depends(require_admin)],
)
async def update_wallet(
    wallet_id: int,
    payload: DistributionWalletUpdate,
    session: AsyncSession = Depends(get_session),
):
    wallet = await session.get(DistributionWallet, wallet_id)
    if wallet is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(wallet, k, v)
    await session.commit()
    await session.refresh(wallet)
    return DistributionWalletOut.model_validate(wallet)


@router.delete(
    "/wallets/{wallet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_wallet(wallet_id: int, session: AsyncSession = Depends(get_session)):
    wallet = await session.get(DistributionWallet, wallet_id)
    if wallet is None:
        raise HTTPException(status_code=404, detail="Wallet not found")
    in_flight = await session.scalar(
        select(func.count())
        .select_from(AirdropSend)
        .where(
            AirdropSend.wallet_id == wallet_id,
            AirdropSend.status.in_(["pending", "broadcast"]),
        )
    )
    if in_flight:
        raise HTTPException(
            status_code=409,
            detail="Wallet has pending or broadcast sends. Stop the worker and reset sends first.",
        )
    await session.delete(wallet)
    await session.commit()
    return None


# ---------------- Worker ----------------

@router.get("/worker", response_model=DistributionWorkerState)
async def get_worker(session: AsyncSession = Depends(get_session)):
    s = distribution_worker.state
    # Use DB-accurate counts so the UI stays correct after resets/restarts.
    confirmed = int(await session.scalar(
        select(func.count()).select_from(AirdropSend).where(AirdropSend.status == "confirmed")
    ) or 0)
    failed = int(await session.scalar(
        select(func.count()).select_from(AirdropSend).where(AirdropSend.status == "failed")
    ) or 0)
    pending = int(await session.scalar(
        select(func.count()).select_from(AirdropSend).where(AirdropSend.status == "pending")
    ) or 0)
    sent = int(await session.scalar(
        select(func.count()).select_from(AirdropSend).where(
            and_(AirdropSend.status.in_(["broadcast", "confirmed", "failed"]), AirdropSend.attempts > 0)
        )
    ) or 0)
    # Read mode and interval from DB so display is accurate even when stopped.
    from backend.services.distribution_service import get_distribution_config
    cfg = await get_distribution_config(session)
    db_mode = cfg.get("distribution_mode") or s.mode
    db_interval = int(cfg.get("distribution_interval_seconds") or s.interval_seconds)
    return DistributionWorkerState(
        enabled=s.enabled,
        running=s.running,
        interval_seconds=db_interval,
        mode=db_mode,
        last_tick_at=s.last_tick_at,
        last_error=s.last_error,
        in_flight=s.in_flight,
        sent_total=sent,
        confirmed_total=confirmed,
        failed_total=failed,
        pending_sends=pending,
    )


@router.post("/worker/start", dependencies=[Depends(require_admin)])
async def start_worker():
    distribution_worker.start()
    return {"ok": True, "running": distribution_worker.state.running}


@router.post("/worker/stop", dependencies=[Depends(require_admin)])
async def stop_worker():
    await distribution_worker.stop()
    return {"ok": True, "running": distribution_worker.state.running}


# ---------------- Config ----------------

@router.get("/config", response_model=DistributionConfigOut)
async def get_distribution_config(session: AsyncSession = Depends(get_session)):
    cfg = await dist.get_distribution_config(session)
    token_id_raw = cfg.get("distribution_token_id", "")
    return DistributionConfigOut(
        distribution_mode=cfg.get("distribution_mode", "all"),
        distribution_token_id=int(token_id_raw) if token_id_raw else None,
        distribution_amount=cfg.get("distribution_amount", "1.0"),
        distribution_interval_seconds=int(cfg.get("distribution_interval_seconds", "10")),
        distribution_max_retries=int(cfg.get("distribution_max_retries", "3")),
        eth_rpc_configured=bool((settings.eth_rpc_url or "").strip()),
        kek_configured=bool((settings.airdrop_kek or "").strip()),
    )


@router.put(
    "/config",
    response_model=DistributionConfigOut,
    dependencies=[Depends(require_admin)],
)
async def update_distribution_config(
    payload: DistributionConfigUpdate,
    session: AsyncSession = Depends(get_session),
):
    updates: dict[str, str] = {}
    data = payload.model_dump(exclude_unset=True)

    if data.get("distribution_mode") is not None:
        updates["distribution_mode"] = data["distribution_mode"]

    if data.get("distribution_token_id") is not None:
        tok = await session.get(AirdropToken, data["distribution_token_id"])
        if tok is None:
            raise HTTPException(status_code=400, detail="Token not found")
        updates["distribution_token_id"] = str(data["distribution_token_id"])

    if data.get("distribution_amount") is not None:
        updates["distribution_amount"] = str(data["distribution_amount"])

    if data.get("distribution_interval_seconds") is not None:
        updates["distribution_interval_seconds"] = str(data["distribution_interval_seconds"])
        distribution_worker.state.interval_seconds = data["distribution_interval_seconds"]

    if data.get("distribution_max_retries") is not None:
        updates["distribution_max_retries"] = str(data["distribution_max_retries"])

    if updates:
        await dist.set_distribution_config(session, updates)

    return await get_distribution_config(session)


# ---------------- Sends ----------------

@router.get("/sends", response_model=AirdropSendListResponse)
async def list_sends(
    status_filter: Optional[str] = Query(None, alias="status"),
    to_address: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    total, rows = await dist.list_sends(
        session,
        status=status_filter,
        to_address=to_address,
        limit=limit,
        offset=offset,
    )
    items: list[AirdropSendOut] = []
    for s in rows:
        item = AirdropSendOut.model_validate(s)
        if s.token:
            item.token_symbol = s.token.symbol
            item.network = s.token.network
        if s.wallet:
            item.wallet_address = s.wallet.address
        items.append(item)
    return AirdropSendListResponse(total=total, limit=limit, offset=offset, items=items)


