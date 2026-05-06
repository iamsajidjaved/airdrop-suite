"""Airdrop monitor & admin API endpoints (Postgres-backed)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_session
from backend.db_models import AirdropConfig, AirdropToken, AirdropTransaction
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
from backend.services.airdrop_monitor import AirdropMonitorService
from backend.services.airdrop_scheduler import scheduler as airdrop_scheduler

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
    )
):
    """Trigger a monitoring pass for all active tokens."""
    logger.info(f"Airdrop monitor run triggered (start_block_override={start_block_override})")
    return await monitor_service.run_monitor(start_block_override=start_block_override)


@router.get("/status", response_model=AirdropStatusResponse)
async def get_status():
    return await monitor_service.get_status()


# ---------------- Scheduler ----------------

@router.get("/scheduler")
async def get_scheduler_state():
    """Return current background scheduler state (running, last run, errors, ETA)."""
    s = airdrop_scheduler.state
    return {
        "enabled": s.enabled,
        "running": s.running,
        "interval_seconds": s.interval_seconds,
        "started_at": s.started_at,
        "last_run_started_at": s.last_run_started_at,
        "last_run_finished_at": s.last_run_finished_at,
        "last_run_duration_seconds": s.last_run_duration_seconds,
        "last_run_inserted": s.last_run_inserted,
        "last_run_errors": s.last_run_errors,
        "last_error": s.last_error,
        "next_run_eta": s.next_run_eta,
        "total_runs": s.total_runs,
        "total_inserted": s.total_inserted,
    }


@router.post("/scheduler/start")
async def start_scheduler():
    airdrop_scheduler.start()
    return {"ok": True, "running": airdrop_scheduler.state.running}


@router.post("/scheduler/stop")
async def stop_scheduler():
    await airdrop_scheduler.stop()
    return {"ok": True, "running": airdrop_scheduler.state.running}


@router.post("/scheduler/trigger", response_model=MonitorRunResult)
async def trigger_scheduler_now():
    """Run a single monitor pass immediately, off the scheduler cadence."""
    return await monitor_service.run_monitor()


# ---------------- Transactions ----------------

@router.get("/transactions", response_model=AirdropTransactionListResponse)
async def list_transactions(
    token: Optional[str] = Query(None, description="Filter by token symbol, e.g. USDT"),
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
