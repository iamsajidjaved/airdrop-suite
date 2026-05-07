"""Airdrop monitor & admin API endpoints (Postgres-backed)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import require_admin
from backend.config import NETWORKS
from backend.db import get_session
from backend.db_models import (
    AirdropConfig,
    AirdropToken,
    AirdropTransaction,
    QualityAddressBlocklist,
    WalletContractCache,
)
from backend.models import (
    AirdropConfigOut,
    AirdropConfigUpdate,
    AirdropStatusResponse,
    AirdropTokenCreate,
    AirdropTokenOut,
    AirdropTokenUpdate,
    AirdropTransactionListResponse,
    AirdropTransactionOut,
    MonitorRunResult,
)
from backend.services import wallet_quality
from backend.services.airdrop_monitor import AirdropMonitorService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/airdrop", tags=["airdrop"])
monitor_service = AirdropMonitorService()

THRESHOLD_KEY = "min_threshold_usd"


# ---------------- Monitor ----------------

@router.post("/monitor/run", response_model=MonitorRunResult)
async def run_monitor(
    start_block_override: Optional[int] = Query(
        None,
        description="Override start block for this run. Token state is NOT updated when set.",
    ),
    network: Optional[str] = Query(
        None,
        description="Restrict the run to tokens on this network (e.g. 'ethereum', 'sepolia'). Default: all active tokens.",
    ),
):
    """Trigger a manual monitoring pass. The scanner runs ONLY when invoked here."""
    logger.info(
        "Airdrop monitor run triggered (start_block_override=%s, network=%s)",
        start_block_override, network,
    )
    return await monitor_service.run_monitor(
        start_block_override=start_block_override, network=network
    )


@router.get("/networks")
async def list_networks():
    """Return the supported networks (used to populate UI selectors)."""
    return [
        {"key": k, "label": v["label"], "chain_id": v["chain_id"], "explorer": v["explorer"]}
        for k, v in NETWORKS.items()
    ]


@router.get("/status", response_model=AirdropStatusResponse)
async def get_status():
    return await monitor_service.get_status()


# ---------------- Transactions ----------------

@router.get("/transactions", response_model=AirdropTransactionListResponse)
async def list_transactions(
    token: Optional[str] = Query(None, description="Filter by token symbol, e.g. USDT"),
    network: Optional[str] = Query(None, description="Filter by network (ethereum, sepolia, ...)"),
    from_address: Optional[str] = Query(None),
    to_address: Optional[str] = Query(None),
    min_amount: Optional[float] = Query(None, ge=0),
    from_date: Optional[str] = Query(None, description="YYYY-MM-DD (UTC)"),
    to_date: Optional[str] = Query(None, description="YYYY-MM-DD (UTC)"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    conditions = []

    if token:
        token_id = await session.scalar(
            select(AirdropToken.id).where(AirdropToken.symbol == token.strip().upper())
        )
        if token_id is None:
            return AirdropTransactionListResponse(total=0, limit=limit, offset=offset, items=[])
        conditions.append(AirdropTransaction.token_id == token_id)

    if network:
        conditions.append(AirdropTransaction.network == network.strip().lower())

    if from_address:
        conditions.append(AirdropTransaction.from_address == from_address.strip().lower())
    if to_address:
        conditions.append(AirdropTransaction.to_address == to_address.strip().lower())
    if min_amount is not None:
        conditions.append(AirdropTransaction.amount >= min_amount)

    def _parse_date(s: str, *, end_of_day: bool) -> datetime:
        try:
            d = datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid date '{s}', expected YYYY-MM-DD")
        if end_of_day:
            d = d.replace(hour=23, minute=59, second=59)
        return d.replace(tzinfo=timezone.utc)

    if from_date:
        conditions.append(AirdropTransaction.transferred_at >= _parse_date(from_date, end_of_day=False))
    if to_date:
        conditions.append(AirdropTransaction.transferred_at <= _parse_date(to_date, end_of_day=True))

    where = and_(*conditions) if conditions else None

    count_stmt = select(func.count()).select_from(AirdropTransaction)
    if where is not None:
        count_stmt = count_stmt.where(where)
    total = await session.scalar(count_stmt) or 0

    list_stmt = (
        select(AirdropTransaction, AirdropToken.symbol)
        .join(AirdropToken, AirdropTransaction.token_id == AirdropToken.id)
        .order_by(AirdropTransaction.transferred_at.desc(), AirdropTransaction.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if where is not None:
        list_stmt = list_stmt.where(where)

    rows = (await session.execute(list_stmt)).all()
    items = []
    for tx, symbol in rows:
        item = AirdropTransactionOut.model_validate(tx)
        item.token_symbol = symbol
        items.append(item)

    return AirdropTransactionListResponse(total=int(total), limit=limit, offset=offset, items=items)


# ---------------- Tokens CRUD ----------------

@router.get("/tokens", response_model=list[AirdropTokenOut])
async def list_tokens(session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(AirdropToken).order_by(AirdropToken.symbol))
    return [AirdropTokenOut.model_validate(t) for t in rows.all()]


@router.post("/tokens", response_model=AirdropTokenOut, status_code=status.HTTP_201_CREATED)
async def create_token(payload: AirdropTokenCreate, session: AsyncSession = Depends(get_session)):
    token = AirdropToken(
        symbol=payload.symbol,
        contract_address=payload.contract_address,
        decimals=payload.decimals,
        network=payload.network,
        is_active=payload.is_active,
    )
    session.add(token)
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Duplicate symbol or contract: {e.orig}") from e
    await session.refresh(token)
    return AirdropTokenOut.model_validate(token)


@router.patch("/tokens/{token_id}", response_model=AirdropTokenOut)
async def update_token(
    token_id: int,
    payload: AirdropTokenUpdate,
    session: AsyncSession = Depends(get_session),
):
    token = await session.get(AirdropToken, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(token, k, v)

    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Duplicate symbol or contract: {e.orig}") from e
    await session.refresh(token)
    return AirdropTokenOut.model_validate(token)


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_token(token_id: int, session: AsyncSession = Depends(get_session)):
    token = await session.get(AirdropToken, token_id)
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    has_tx = await session.scalar(
        select(func.count()).select_from(AirdropTransaction).where(AirdropTransaction.token_id == token_id)
    )
    if has_tx:
        raise HTTPException(
            status_code=409,
            detail="Token has stored transactions. Set is_active=false instead of deleting.",
        )
    await session.delete(token)
    await session.commit()
    return None


# ---------------- Config ----------------

@router.get("/config", response_model=AirdropConfigOut)
async def get_config(session: AsyncSession = Depends(get_session)):
    raw = await session.scalar(select(AirdropConfig.value).where(AirdropConfig.key == THRESHOLD_KEY))
    try:
        threshold = float(raw) if raw is not None else 500.0
    except (TypeError, ValueError):
        threshold = 500.0
    return AirdropConfigOut(min_threshold_usd=threshold)


@router.put("/config", response_model=AirdropConfigOut)
async def update_config(payload: AirdropConfigUpdate, session: AsyncSession = Depends(get_session)):
    cfg = await session.get(AirdropConfig, THRESHOLD_KEY)
    if cfg is None:
        cfg = AirdropConfig(key=THRESHOLD_KEY, value=str(payload.min_threshold_usd))
        session.add(cfg)
    else:
        cfg.value = str(payload.min_threshold_usd)
    await session.commit()
    return AirdropConfigOut(min_threshold_usd=payload.min_threshold_usd)


# ---------------- Quality filter ----------------

@router.get("/quality/stats")
async def quality_stats(session: AsyncSession = Depends(get_session)):
    """High-level counts: blocklist size, contract cache, transactions retained."""
    block_total = await session.scalar(
        select(func.count()).select_from(QualityAddressBlocklist)
    )
    cache_total = await session.scalar(select(func.count()).select_from(WalletContractCache))
    contract_total = await session.scalar(
        select(func.count())
        .select_from(WalletContractCache)
        .where(WalletContractCache.is_contract.is_(True))
    )
    tx_total = await session.scalar(select(func.count()).select_from(AirdropTransaction))
    distinct_recipients = await session.scalar(
        select(func.count(func.distinct(AirdropTransaction.to_address)))
    )
    return {
        "blocklist_entries": int(block_total or 0),
        "contract_cache_entries": int(cache_total or 0),
        "known_contracts": int(contract_total or 0),
        "airdrop_transactions": int(tx_total or 0),
        "distinct_recipients": int(distinct_recipients or 0),
    }


@router.get("/quality/blocklist")
async def list_blocklist(
    network: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(QualityAddressBlocklist).order_by(QualityAddressBlocklist.added_at.desc())
    if network:
        stmt = stmt.where(QualityAddressBlocklist.network == network)
    total = await session.scalar(
        select(func.count())
        .select_from(QualityAddressBlocklist)
        .where(QualityAddressBlocklist.network == network) if network
        else select(func.count()).select_from(QualityAddressBlocklist)
    )
    rows = (await session.scalars(stmt.limit(limit).offset(offset))).all()
    return {
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r.id,
                "network": r.network,
                "address": r.address,
                "reason": r.reason,
                "source": r.source,
                "added_at": r.added_at.isoformat() if r.added_at else None,
            }
            for r in rows
        ],
    }


@router.post("/quality/blocklist", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def add_blocklist_entry(
    address: str = Query(..., min_length=4, max_length=64),
    reason: str = Query(..., min_length=1, max_length=64),
    network: str = Query("ethereum", max_length=32),
    source: str = Query("manual", max_length=64),
    session: AsyncSession = Depends(get_session),
):
    await wallet_quality.add_to_blocklist(
        session, address=address, reason=reason, network=network, source=source
    )
    return {"ok": True, "address": address.lower(), "network": network}


@router.delete("/quality/blocklist", dependencies=[Depends(require_admin)])
async def delete_blocklist_entry(
    address: str = Query(...),
    network: str = Query("ethereum"),
    session: AsyncSession = Depends(get_session),
):
    deleted = await wallet_quality.remove_from_blocklist(
        session, address=address, network=network
    )
    return {"ok": True, "deleted": deleted}


@router.post("/quality/prune", dependencies=[Depends(require_admin)])
async def trigger_prune(
    network: str = Query("ethereum"),
    enrich: bool = Query(True, description="Run eth_getCode on unknown to_addresses first"),
    enrich_limit: int = Query(5000, ge=0, le=50000),
    session: AsyncSession = Depends(get_session),
):
    """Manually re-run the quality prune pipeline against the current DB."""
    enrich_summary: dict[str, int] = {}
    if enrich and enrich_limit > 0:
        unknown = await session.scalars(
            select(AirdropTransaction.to_address)
            .where(AirdropTransaction.network == network)
            .where(
                AirdropTransaction.to_address.notin_(
                    select(WalletContractCache.address).where(
                        WalletContractCache.network == network
                    )
                )
            )
            .distinct()
            .limit(enrich_limit)
        )
        addrs = list(unknown.all())
        if addrs:
            enrich_summary = await wallet_quality.enrich_contracts(
                session, addrs, network=network
            )

    contract_pruned = await wallet_quality.prune_contract_recipients(session, network)
    aggregate = await wallet_quality.prune_low_quality(session, network)
    return {
        "network": network,
        "enriched": enrich_summary,
        "contract_pruned": contract_pruned,
        "aggregate_pruned": aggregate,
    }


# ---------------- Operational reset ----------------

@router.post("/admin/reset", dependencies=[Depends(require_admin)])
async def reset_data(
    include_blocklist: bool = Query(False, description="Also truncate quality_address_blocklist"),
    include_wallets: bool = Query(False, description="Also truncate distribution_wallets (DESTRUCTIVE: encrypted private keys are lost)"),
):
    """Wipe collected runtime data so a fresh scan can start from a clean slate.

    Always truncates: airdrop_transactions, wallet_contract_cache,
    distribution_campaigns, distribution_recipients, distribution_transactions.
    Always resets last_scanned_block on every airdrop_tokens row.

    Schema and operator-managed config (token list, threshold) are preserved.
    """
    from backend.services.reset_service import reset_collected_data

    return await reset_collected_data(
        include_blocklist=include_blocklist,
        include_wallets=include_wallets,
    )

