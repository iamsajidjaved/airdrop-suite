"""Distribution API endpoints (Phase 2)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import require_admin
from backend.config import settings
from backend.db import get_session
from backend.db_models import (
    AirdropToken,
    DistributionCampaign,
    DistributionWallet,
)
from backend.models import (
    CampaignBuildResult,
    DistributionCampaignCreate,
    DistributionCampaignOut,
    DistributionConfigOut,
    DistributionRecipientListResponse,
    DistributionRecipientOut,
    DistributionWalletCreate,
    DistributionWalletOut,
    DistributionWalletUpdate,
    DistributionWorkerState,
)
from backend.services import distribution_service as dist
from backend.services.crypto import CryptoUnavailable
from backend.services.distribution_worker import worker as distribution_worker
from backend.services.web3_client import (
    Web3Unavailable,
    get_eth_balance,
    get_token_balance,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/distribution", tags=["distribution"])


# ---------------- Wallets ----------------

@router.get("/wallets", response_model=list[DistributionWalletOut])
async def list_wallets(
    include_balances: bool = Query(False),
    session: AsyncSession = Depends(get_session),
):
    wallets = await dist.list_wallets(session)
    out: list[DistributionWalletOut] = []

    token_rows: list[AirdropToken] = []
    if include_balances:
        token_rows = list(
            (await session.scalars(select(AirdropToken).where(AirdropToken.is_active.is_(True)))).all()
        )

    for w in wallets:
        item = DistributionWalletOut.model_validate(w)
        if include_balances:
            try:
                item.eth_balance = await get_eth_balance(w.address)
                token_balances: dict[str, float] = {}
                for t in token_rows:
                    try:
                        bal = await get_token_balance(t.contract_address, w.address, t.decimals)
                        token_balances[t.symbol] = bal
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "token balance failed for %s/%s: %s: %r",
                            w.address, t.symbol, type(e).__name__, e,
                        )
                item.token_balances = token_balances  # type: ignore[assignment]
            except Web3Unavailable as e:
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "eth balance failed for %s: %s: %r",
                    w.address, type(e).__name__, e,
                )
        out.append(item)
    return out


@router.post(
    "/wallets",
    response_model=DistributionWalletOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_wallet(payload: DistributionWalletCreate, session: AsyncSession = Depends(get_session)):
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
    # Soft-guard: don't delete if any in-flight broadcasts reference this wallet.
    from backend.db_models import DistributionTransaction
    from sqlalchemy import func as sa_func

    in_flight = await session.scalar(
        select(sa_func.count())
        .select_from(DistributionTransaction)
        .where(
            DistributionTransaction.wallet_id == wallet_id,
            DistributionTransaction.status == "broadcast",
        )
    )
    if in_flight:
        raise HTTPException(status_code=409, detail="Wallet has broadcast transactions awaiting confirmation.")
    await session.delete(wallet)
    await session.commit()
    return None


# ---------------- Campaigns ----------------

async def _campaign_to_out(session: AsyncSession, c: DistributionCampaign) -> DistributionCampaignOut:
    out = DistributionCampaignOut.model_validate(c)
    if c.token is not None:
        out.token_symbol = c.token.symbol
    out.counts = await dist.campaign_counts(session, c.id)
    out.sender_wallet_ids = await dist.get_campaign_wallet_ids(session, c.id)
    return out


@router.get("/campaigns", response_model=list[DistributionCampaignOut])
async def list_campaigns(session: AsyncSession = Depends(get_session)):
    campaigns = await dist.list_campaigns(session)
    return [await _campaign_to_out(session, c) for c in campaigns]


@router.get("/campaigns/{campaign_id}", response_model=DistributionCampaignOut)
async def get_campaign(campaign_id: int, session: AsyncSession = Depends(get_session)):
    c = await dist.get_campaign(session, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return await _campaign_to_out(session, c)


@router.post(
    "/campaigns",
    response_model=DistributionCampaignOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_campaign(payload: DistributionCampaignCreate, session: AsyncSession = Depends(get_session)):
    try:
        campaign = await dist.create_campaign(
            session,
            name=payload.name,
            token_id=payload.token_id,
            amount_per_recipient=payload.amount_per_recipient,
            recipient_filter=payload.recipient_filter.model_dump(),
            max_total_amount=payload.max_total_amount,
            dry_run=payload.dry_run,
            sender_mode=payload.sender_mode,
            sender_wallet_ids=payload.sender_wallet_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await _campaign_to_out(session, campaign)


@router.post(
    "/campaigns/{campaign_id}/build",
    response_model=CampaignBuildResult,
    dependencies=[Depends(require_admin)],
)
async def build_campaign(campaign_id: int, session: AsyncSession = Depends(get_session)):
    try:
        inserted, total = await dist.build_recipients(session, campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return CampaignBuildResult(campaign_id=campaign_id, inserted=inserted, total_recipients=total)


@router.post(
    "/campaigns/{campaign_id}/start",
    response_model=DistributionCampaignOut,
    dependencies=[Depends(require_admin)],
)
async def start_campaign(campaign_id: int, session: AsyncSession = Depends(get_session)):
    c = await dist.get_campaign(session, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if c.dry_run:
        raise HTTPException(status_code=400, detail="Campaign is in dry_run mode; flip dry_run=false first.")
    if c.status not in ("ready", "paused"):
        raise HTTPException(status_code=400, detail=f"Cannot start from status '{c.status}'.")
    c = await dist.transition_campaign(session, campaign_id, "running")
    return await _campaign_to_out(session, c)


@router.post(
    "/campaigns/{campaign_id}/pause",
    response_model=DistributionCampaignOut,
    dependencies=[Depends(require_admin)],
)
async def pause_campaign(campaign_id: int, session: AsyncSession = Depends(get_session)):
    c = await dist.get_campaign(session, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if c.status != "running":
        raise HTTPException(status_code=400, detail=f"Cannot pause from status '{c.status}'.")
    c = await dist.transition_campaign(session, campaign_id, "paused")
    return await _campaign_to_out(session, c)


@router.post(
    "/campaigns/{campaign_id}/retry-failed",
    dependencies=[Depends(require_admin)],
)
async def retry_failed(campaign_id: int, session: AsyncSession = Depends(get_session)):
    n = await dist.reset_failed_recipients(session, campaign_id)
    return {"reset": n}


@router.patch(
    "/campaigns/{campaign_id}",
    response_model=DistributionCampaignOut,
    dependencies=[Depends(require_admin)],
)
async def patch_campaign(
    campaign_id: int,
    payload: dict,
    session: AsyncSession = Depends(get_session),
):
    """Limited PATCH: toggle dry_run, change name, max_total_amount, sender_mode.

    To replace the assigned sender wallet set, use
    `PUT /campaigns/{id}/wallets`.
    """
    c = await dist.get_campaign(session, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    allowed = {"dry_run", "name", "max_total_amount", "sender_mode"}
    for k, v in payload.items():
        if k in allowed:
            if k == "sender_mode" and v not in ("single", "multi"):
                raise HTTPException(status_code=400, detail="sender_mode must be 'single' or 'multi'")
            setattr(c, k, v)
    await session.commit()
    await session.refresh(c)
    return await _campaign_to_out(session, c)


@router.put(
    "/campaigns/{campaign_id}/wallets",
    response_model=DistributionCampaignOut,
    dependencies=[Depends(require_admin)],
)
async def set_campaign_wallets(
    campaign_id: int,
    payload: dict,
    session: AsyncSession = Depends(get_session),
):
    """Replace the sender-wallet assignment for a campaign.

    Body: ``{"sender_wallet_ids": [1, 2, 3]}``. Empty list clears the
    assignment, causing the worker to fall back to all active wallets.
    """
    c = await dist.get_campaign(session, campaign_id)
    if c is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    ids = payload.get("sender_wallet_ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="sender_wallet_ids must be a list of ints")
    try:
        await dist.set_campaign_wallets(session, campaign_id, [int(i) for i in ids])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await session.refresh(c)
    return await _campaign_to_out(session, c)


# ---------------- Recipients ----------------

@router.get(
    "/campaigns/{campaign_id}/recipients",
    response_model=DistributionRecipientListResponse,
)
async def list_recipients(
    campaign_id: int,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    total, rows = await dist.list_recipients(
        session, campaign_id, status=status_filter, limit=limit, offset=offset
    )
    items: list[DistributionRecipientOut] = []
    for r, last_tx in rows:
        item = DistributionRecipientOut.model_validate(r)
        item.last_tx_hash = last_tx
        items.append(item)
    return DistributionRecipientListResponse(total=total, limit=limit, offset=offset, items=items)


# ---------------- Worker ----------------

@router.get("/worker", response_model=DistributionWorkerState)
async def get_worker():
    s = distribution_worker.state
    return DistributionWorkerState(
        enabled=s.enabled,
        running=s.running,
        interval_seconds=s.interval_seconds,
        last_tick_at=s.last_tick_at,
        last_error=s.last_error,
        in_flight=s.in_flight,
        sent_total=s.sent_total,
        confirmed_total=s.confirmed_total,
        failed_total=s.failed_total,
    )


@router.post("/worker/start", dependencies=[Depends(require_admin)])
async def start_worker():
    distribution_worker.start()
    return {"ok": True, "running": distribution_worker.state.running}


@router.post("/worker/stop", dependencies=[Depends(require_admin)])
async def stop_worker():
    await distribution_worker.stop()
    return {"ok": True, "running": distribution_worker.state.running}


# ---------------- Config (read-only) ----------------

@router.get("/config", response_model=DistributionConfigOut)
async def get_distribution_config():
    return DistributionConfigOut(
        eth_rpc_configured=bool((settings.eth_rpc_url or "").strip()),
        kek_configured=bool((settings.airdrop_kek or "").strip()),
        admin_token_required=bool((settings.airdrop_admin_token or "").strip()),
        max_gas_price_gwei=settings.distribution_max_gas_price_gwei,
        per_wallet_daily_cap=settings.distribution_per_wallet_daily_cap,
        max_inflight=settings.distribution_max_inflight,
        receipt_poll_seconds=settings.distribution_receipt_poll_seconds,
        max_retries_per_recipient=settings.distribution_max_retries_per_recipient,
    )
