"""Airdrop monitor API endpoints."""
import logging
from typing import Optional

from fastapi import APIRouter, Query

from backend.models import AirdropRecipient, AirdropStatusResponse, MonitorRunResult
from backend.services.airdrop_monitor import AirdropMonitorService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/airdrop", tags=["airdrop"])
monitor_service = AirdropMonitorService()


@router.post("/monitor/run", response_model=MonitorRunResult)
async def run_monitor(
    start_block_override: Optional[int] = Query(
        None,
        description="Override start block for this run. State is NOT updated when set (re-scan mode).",
    )
):
    """Trigger a monitoring pass. Fetches recent ERC-20 transfers for configured tokens,
    filters by USD threshold, and saves qualifying recipient addresses to disk."""
    logger.info(f"Airdrop monitor run triggered (start_block_override={start_block_override})")
    return await monitor_service.run_monitor(start_block_override=start_block_override)


@router.get("/recipients", response_model=list[AirdropRecipient])
async def get_recipients(
    token: Optional[str] = Query(None, description="Filter by token symbol, e.g. USDT"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Return stored recipient addresses. Does not trigger a new scan."""
    return monitor_service.get_recipients(token_filter=token, limit=limit, offset=offset)


@router.get("/status", response_model=AirdropStatusResponse)
async def get_status():
    """Return monitor state: last run timestamp, last processed block per token, recipient count."""
    return monitor_service.get_status()
