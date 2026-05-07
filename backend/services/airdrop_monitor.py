"""Airdrop monitor service — Postgres-backed.

Scans configured ERC-20 token contracts for transfers ≥ the configured USD
threshold and persists qualifying transfers to ``airdrop_transactions``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import chain_id_for, settings
from backend.db import async_session_factory
from backend.db_models import AirdropConfig, AirdropToken, AirdropTransaction, WalletContractCache
from backend.models import AirdropStatusResponse, MonitorRunResult
from backend.services import wallet_quality
from backend.services.etherscan import EtherscanService, etherscan_service

logger = logging.getLogger(__name__)

MAX_PAGES_PER_RUN = 10
DEFAULT_THRESHOLD_USD = 500.0


class _TokenSpec:
    """Lightweight snapshot of an AirdropToken for use outside a session."""

    __slots__ = ("id", "symbol", "contract", "decimals", "network", "start_block")

    def __init__(self, id: int, symbol: str, contract: str, decimals: int, network: str, start_block: int):
        self.id = id
        self.symbol = symbol
        self.contract = contract
        self.decimals = decimals
        self.network = network
        self.start_block = start_block


class AirdropMonitorService:
    def __init__(self, etherscan: Optional[EtherscanService] = None):
        self._etherscan = etherscan or etherscan_service
        self._page_size = settings.airdrop_page_size

    async def _load_threshold(self, session: AsyncSession) -> float:
        row = await session.scalar(
            select(AirdropConfig.value).where(AirdropConfig.key == "min_threshold_usd")
        )
        if row is None:
            return DEFAULT_THRESHOLD_USD
        try:
            return float(row)
        except (TypeError, ValueError):
            return DEFAULT_THRESHOLD_USD

    async def _load_active_tokens(self, session: AsyncSession) -> list[AirdropToken]:
        result = await session.scalars(
            select(AirdropToken).where(AirdropToken.is_active.is_(True)).order_by(AirdropToken.id)
        )
        return list(result.all())

    def _filter_and_extract(
        self,
        txs: list[dict],
        spec: _TokenSpec,
        threshold: float,
    ) -> list[dict]:
        results: list[dict] = []
        scale = Decimal(10) ** spec.decimals
        threshold_dec = Decimal(str(threshold))
        for tx in txs:
            try:
                amount = Decimal(tx.get("value", "0")) / scale
                if amount < threshold_dec:
                    continue
                ts = int(tx.get("timeStamp", 0))
                results.append({
                    "tx_hash": tx.get("hash", ""),
                    "log_index": int(tx.get("logIndex", 0) or 0),
                    "block_number": int(tx.get("blockNumber", 0)),
                    "network": spec.network,
                    "token_id": spec.id,
                    "from_address": (tx.get("from") or "").lower(),
                    "to_address": (tx.get("to") or "").lower(),
                    "amount": amount,
                    "amount_usd": amount,  # stablecoins: 1:1
                    "transferred_at": datetime.fromtimestamp(ts, tz=timezone.utc),
                })
            except Exception as e:
                logger.warning(f"Skipping tx due to parse error: {e}")
        return results

    @staticmethod
    def _apply_quality_gate_a(
        rows: list[dict],
        *,
        blocklist: set[str],
        known_contracts: set[str],
        aggregator_threshold: int,
    ) -> tuple[list[dict], dict[str, int]]:
        """Drop rows whose recipient is obviously low-quality, before insert.

        Returns (kept_rows, drop_counts). Counts are reported per reason.
        """
        if not rows:
            return rows, {"blocklist": 0, "contract": 0, "self_tx": 0, "aggregator": 0}

        aggregators = wallet_quality.in_batch_aggregator_set(rows, aggregator_threshold)
        kept: list[dict] = []
        counts = {"blocklist": 0, "contract": 0, "self_tx": 0, "aggregator": 0}
        for r in rows:
            to_addr = r["to_address"]
            if to_addr in blocklist:
                counts["blocklist"] += 1
                continue
            if to_addr in known_contracts:
                counts["contract"] += 1
                continue
            if to_addr == r["from_address"]:
                counts["self_tx"] += 1
                continue
            if to_addr in aggregators:
                counts["aggregator"] += 1
                continue
            kept.append(r)
        return kept, counts

    async def _fetch_all_pages(
        self, contract: str, start_block: int, chain_id: int
    ) -> tuple[list[dict], int]:
        """Fetch up to MAX_PAGES_PER_RUN pages. Returns (txs, max_block_seen)."""
        all_txs: list[dict] = []
        max_block = start_block

        for page in range(1, MAX_PAGES_PER_RUN + 1):
            try:
                txs = await self._etherscan.get_contract_token_transfers(
                    contract_address=contract,
                    start_block=start_block,
                    page=page,
                    offset=self._page_size,
                    chain_id=chain_id,
                )
            except Exception as e:
                logger.error(f"Error fetching page {page} for {contract}: {e}")
                break

            if not txs:
                break

            all_txs.extend(txs)

            for tx in txs:
                try:
                    max_block = max(max_block, int(tx.get("blockNumber", 0)))
                except Exception:
                    pass

            if len(txs) < self._page_size:
                break

        return all_txs, max_block

    async def run_monitor(
        self,
        start_block_override: Optional[int] = None,
        network: Optional[str] = None,
    ) -> MonitorRunResult:
        now_str = datetime.now(tz=timezone.utc).isoformat()
        errors: list[str] = []
        tokens_scanned: list[str] = []
        blocks_scanned_per_token: dict[str, dict] = {}
        new_count = 0

        quality_enabled = settings.quality_filter_enabled
        quality_drop_totals: dict[str, int] = {
            "blocklist": 0,
            "contract": 0,
            "self_tx": 0,
            "aggregator": 0,
        }
        quality_enrich_summary: dict[str, int] = {}
        quality_prune_summary: dict[str, int] = {}

        # 1) Load config & token specs
        async with async_session_factory() as session:
            tokens = await self._load_active_tokens(session)
            if network:
                net = network.strip().lower()
                tokens = [t for t in tokens if (t.network or "").lower() == net]
            threshold = await self._load_threshold(session)

            if not tokens:
                logger.warning("No active airdrop tokens configured.")
                total = await session.scalar(select(func.count()).select_from(AirdropTransaction)) or 0
                return MonitorRunResult(
                    tokens_scanned=[],
                    new_transfers_inserted=0,
                    total_transfers_stored=int(total),
                    blocks_scanned_per_token={},
                    run_timestamp=now_str,
                    errors=["No active tokens configured"],
                )

            specs = [
                _TokenSpec(
                    id=t.id,
                    symbol=t.symbol,
                    contract=t.contract_address,
                    decimals=t.decimals,
                    network=t.network,
                    start_block=(
                        start_block_override
                        if start_block_override is not None
                        else (t.last_scanned_block or 0)
                    ),
                )
                for t in tokens
            ]

        # 2) Fetch concurrently (no DB session held). Each token uses the
        # Etherscan v2 chain id derived from its network column, so mainnet
        # and Sepolia tokens can be scanned in the same pass.
        results = await asyncio.gather(
            *(
                self._fetch_all_pages(
                    spec.contract, spec.start_block, chain_id_for(spec.network)
                )
                for spec in specs
            ),
            return_exceptions=True,
        )

        # 3) Persist serially in one transaction
        async with async_session_factory() as session:
            # Load Gate A inputs once per pass (per network as encountered).
            blocklist_by_net: dict[str, set[str]] = {}
            contracts_by_net: dict[str, set[str]] = {}
            if quality_enabled:
                for spec in specs:
                    if spec.network not in blocklist_by_net:
                        blocklist_by_net[spec.network] = await wallet_quality.load_blocklist(
                            session, spec.network
                        )
                        contracts_by_net[spec.network] = await wallet_quality.load_known_contracts(
                            session, spec.network
                        )

            for spec, result in zip(specs, results):
                tokens_scanned.append(spec.symbol)

                if isinstance(result, Exception):
                    msg = f"{spec.symbol}: {type(result).__name__}: {result}"
                    logger.error(msg)
                    errors.append(msg)
                    blocks_scanned_per_token[spec.symbol] = {
                        "from": spec.start_block,
                        "to": spec.start_block,
                        "fetched": 0,
                    }
                    continue

                txs, max_block = result
                blocks_scanned_per_token[spec.symbol] = {
                    "from": spec.start_block,
                    "to": max_block,
                    "fetched": len(txs),
                }

                rows = self._filter_and_extract(txs, spec, threshold)

                if quality_enabled and rows:
                    rows, drop_counts = self._apply_quality_gate_a(
                        rows,
                        blocklist=blocklist_by_net.get(spec.network, set()),
                        known_contracts=contracts_by_net.get(spec.network, set()),
                        aggregator_threshold=settings.quality_per_run_aggregator_drop_threshold,
                    )
                    for k, v in drop_counts.items():
                        quality_drop_totals[k] += v
                    blocks_scanned_per_token[spec.symbol]["quality_dropped"] = sum(
                        drop_counts.values()
                    )

                if rows:
                    # PostgreSQL has a hard limit of 32767 bind parameters per
                    # statement. Each row has 10 columns, so cap each batch at
                    # 3000 rows (= 30000 params) to stay safely under the limit.
                    BATCH_SIZE = 3000
                    for i in range(0, len(rows), BATCH_SIZE):
                        chunk = rows[i : i + BATCH_SIZE]
                        stmt = (
                            pg_insert(AirdropTransaction.__table__)
                            .values(chunk)
                            .on_conflict_do_nothing(constraint="uq_airdrop_tx_hash_log_token")
                        )
                        res = await session.execute(stmt)
                        new_count += res.rowcount or 0

                if start_block_override is None and max_block > spec.start_block:
                    await session.execute(
                        AirdropToken.__table__.update()
                        .where(AirdropToken.id == spec.id)
                        .values(last_scanned_block=max_block + 1)
                    )

            await session.commit()

        # 4) Gate B: enrich newly-seen recipients via eth_getCode, then prune.
        if quality_enabled:
            networks_seen = {spec.network for spec in specs}
            async with async_session_factory() as session:
                for net in networks_seen:
                    # Pull recent unknown to_addresses for this network.
                    if settings.quality_contract_check_enabled:
                        unknown_addrs = await session.scalars(
                            select(AirdropTransaction.to_address)
                            .where(AirdropTransaction.network == net)
                            .where(
                                AirdropTransaction.to_address.notin_(
                                    select(WalletContractCache.address).where(
                                        WalletContractCache.network == net
                                    )
                                )
                            )
                            .distinct()
                            .limit(5000)
                        )
                        addrs = list(unknown_addrs.all())
                        if addrs:
                            try:
                                summary = await wallet_quality.enrich_contracts(
                                    session, addrs, network=net
                                )
                                for k, v in summary.items():
                                    quality_enrich_summary[k] = (
                                        quality_enrich_summary.get(k, 0) + v
                                    )
                            except Exception as e:  # noqa: BLE001
                                msg = f"contract-enrich {net}: {type(e).__name__}: {e}"
                                logger.warning(msg)
                                errors.append(msg)

                    try:
                        contract_pruned = await wallet_quality.prune_contract_recipients(
                            session, net
                        )
                        prune = await wallet_quality.prune_low_quality(session, net)
                        prune["contract"] = contract_pruned
                        prune["rows_deleted"] = prune.get("rows_deleted", 0) + contract_pruned
                        for k, v in prune.items():
                            quality_prune_summary[k] = quality_prune_summary.get(k, 0) + v
                    except Exception as e:  # noqa: BLE001
                        msg = f"prune {net}: {type(e).__name__}: {e}"
                        logger.warning(msg)
                        errors.append(msg)

        async with async_session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(AirdropTransaction)) or 0

        logger.info(
            "Monitor run complete. inserted=%s total=%s gateA_drops=%s enrich=%s prune=%s",
            new_count,
            total,
            quality_drop_totals,
            quality_enrich_summary,
            quality_prune_summary,
        )

        # Surface quality stats via the existing errors[] channel so callers
        # can see them without a model migration.
        if quality_enabled:
            errors.append(
                "quality: gateA_dropped="
                + ",".join(f"{k}={v}" for k, v in quality_drop_totals.items() if v)
                + " enrich="
                + ",".join(f"{k}={v}" for k, v in quality_enrich_summary.items() if v)
                + " pruned="
                + ",".join(f"{k}={v}" for k, v in quality_prune_summary.items() if v)
            )

        return MonitorRunResult(
            tokens_scanned=tokens_scanned,
            new_transfers_inserted=new_count,
            total_transfers_stored=int(total),
            blocks_scanned_per_token=blocks_scanned_per_token,
            run_timestamp=now_str,
            errors=errors,
        )

    async def get_status(self) -> AirdropStatusResponse:
        async with async_session_factory() as session:
            tokens = await session.scalars(select(AirdropToken).order_by(AirdropToken.symbol))
            last_block_per_token: dict[str, int] = {
                t.symbol: int(t.last_scanned_block or 0) for t in tokens.all()
            }
            total = await session.scalar(select(func.count()).select_from(AirdropTransaction)) or 0
            last_run = await session.scalar(select(func.max(AirdropTransaction.created_at)))

        return AirdropStatusResponse(
            last_run_timestamp=last_run.isoformat() if last_run else None,
            last_block_per_token=last_block_per_token,
            total_transfers=int(total),
        )


# Module-level singleton (kept for compatibility with existing imports)
monitor_service = AirdropMonitorService()
