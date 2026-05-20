"""Airdrop monitor service — Postgres-backed.

Two scan modes:
- standard: scans configured ERC-20 token contracts for transfers ≥ the
  configured USD threshold and persists qualifying transfers.
- igaming: scans outgoing ERC-20 transfers FROM configured iGaming brand
  wallets to identify their users as airdrop targets.
- both: runs both modes in a single pass.

The active blockchain network is read from airdrop_config (active_network key)
rather than being stored per-token.  This gives a single universal control
in the admin UI.
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

from backend.config import chain_id_for, get_active_network, settings
from backend.db import async_session_factory
from backend.db_models import (
    AirdropConfig,
    AirdropToken,
    AirdropTransaction,
    IgamingBrand,
    WalletContractCache,
)
from backend.models import AirdropStatusResponse, MonitorRunResult
from backend.services import wallet_quality
from backend.services.etherscan import EtherscanService, etherscan_service

logger = logging.getLogger(__name__)

MAX_PAGES_PER_RUN = 10
DEFAULT_THRESHOLD_USD = 500.0
DEFAULT_IGAMING_THRESHOLD_USD = 0.0

VALID_SCAN_MODES = {"standard", "igaming", "both"}


class _TokenSpec:
    """Lightweight snapshot of an AirdropToken for use outside a session."""

    __slots__ = ("id", "symbol", "contract", "decimals", "start_block")

    def __init__(self, id: int, symbol: str, contract: str, decimals: int, start_block: int):
        self.id = id
        self.symbol = symbol
        self.contract = contract
        self.decimals = decimals
        self.start_block = start_block


class _BrandSpec:
    """Lightweight snapshot of an IgamingBrand for use outside a session."""

    __slots__ = ("id", "name", "wallet_address", "start_block")

    def __init__(self, id: int, name: str, wallet_address: str, start_block: int):
        self.id = id
        self.name = name
        self.wallet_address = wallet_address
        self.start_block = start_block


class AirdropMonitorService:
    def __init__(self, etherscan: Optional[EtherscanService] = None):
        self._etherscan = etherscan or etherscan_service
        self._page_size = settings.airdrop_page_size

    # ------------------------------------------------------------------
    # DB loaders
    # ------------------------------------------------------------------

    async def _load_threshold(self, session: AsyncSession) -> float:
        row = await session.scalar(
            select(AirdropConfig.value).where(AirdropConfig.key == "min_threshold_usd")
        )
        try:
            return float(row) if row is not None else DEFAULT_THRESHOLD_USD
        except (TypeError, ValueError):
            return DEFAULT_THRESHOLD_USD

    async def _load_igaming_threshold(self, session: AsyncSession) -> float:
        row = await session.scalar(
            select(AirdropConfig.value).where(AirdropConfig.key == "igaming_threshold_usd")
        )
        try:
            return float(row) if row is not None else DEFAULT_IGAMING_THRESHOLD_USD
        except (TypeError, ValueError):
            return DEFAULT_IGAMING_THRESHOLD_USD

    async def _load_active_tokens(self, session: AsyncSession) -> list[AirdropToken]:
        result = await session.scalars(
            select(AirdropToken).where(AirdropToken.is_active.is_(True)).order_by(AirdropToken.id)
        )
        return list(result.all())

    async def _load_active_brands(self, session: AsyncSession) -> list[IgamingBrand]:
        result = await session.scalars(
            select(IgamingBrand).where(IgamingBrand.is_active.is_(True)).order_by(IgamingBrand.id)
        )
        return list(result.all())

    # ------------------------------------------------------------------
    # Standard scan helpers
    # ------------------------------------------------------------------

    def _filter_and_extract(
        self,
        txs: list[dict],
        spec: _TokenSpec,
        network: str,
        threshold: float,
    ) -> list[dict]:
        """Filter raw Etherscan rows and normalise into DB-ready dicts (standard mode)."""
        results: list[dict] = []
        scale = Decimal(10) ** spec.decimals
        threshold_dec = Decimal(str(threshold))
        _ZERO_ADDR = "0x0000000000000000000000000000000000000000"
        for tx in txs:
            try:
                amount = Decimal(tx.get("value", "0")) / scale
                if amount < threshold_dec:
                    continue
                to_addr = (tx.get("to") or "").lower()
                from_addr = (tx.get("from") or "").lower()
                if not to_addr or to_addr == _ZERO_ADDR or not from_addr:
                    continue
                ts = int(tx.get("timeStamp", 0))
                results.append({
                    "tx_hash": tx.get("hash", ""),
                    "log_index": int(tx.get("logIndex", 0) or 0),
                    "block_number": int(tx.get("blockNumber", 0)),
                    "network": network,
                    "token_id": spec.id,
                    "from_address": from_addr,
                    "to_address": to_addr,
                    "amount": amount,
                    "amount_usd": amount,
                    "transferred_at": datetime.fromtimestamp(ts, tz=timezone.utc),
                    "scan_mode": "standard",
                    "igaming_brand_id": None,
                })
            except Exception as e:
                logger.warning("Skipping tx due to parse error: %s", e)
        return results

    @staticmethod
    def _apply_quality_gate_a(
        rows: list[dict],
        *,
        blocklist: set[str],
        known_contracts: set[str],
        aggregator_threshold: int,
        token_contracts: set[str],
        from_aggregator_threshold: int,
    ) -> tuple[list[dict], dict[str, int]]:
        """Drop low-quality rows before insert (standard mode only)."""
        if not rows:
            return rows, {
                "blocklist": 0, "from_blocklist": 0, "mint": 0,
                "contract": 0, "self_tx": 0, "aggregator": 0, "from_aggregator": 0,
            }

        aggregators = wallet_quality.in_batch_aggregator_set(rows, aggregator_threshold)
        from_aggregators = wallet_quality.in_batch_from_aggregator_set(rows, from_aggregator_threshold)
        kept: list[dict] = []
        counts = {
            "blocklist": 0, "from_blocklist": 0, "mint": 0,
            "contract": 0, "self_tx": 0, "aggregator": 0, "from_aggregator": 0,
        }
        for r in rows:
            to_addr = r["to_address"]
            from_addr = r["from_address"]
            if to_addr in blocklist:
                counts["blocklist"] += 1
                continue
            if from_addr in blocklist:
                counts["from_blocklist"] += 1
                continue
            if from_addr in token_contracts:
                counts["mint"] += 1
                continue
            if to_addr in known_contracts:
                counts["contract"] += 1
                continue
            if to_addr == from_addr:
                counts["self_tx"] += 1
                continue
            if to_addr in aggregators:
                counts["aggregator"] += 1
                continue
            if from_addr in from_aggregators:
                counts["from_aggregator"] += 1
                continue
            kept.append(r)
        return kept, counts

    async def _fetch_all_pages(
        self, contract: str, start_block: int, chain_id: int
    ) -> tuple[list[dict], int]:
        """Fetch up to MAX_PAGES_PER_RUN pages for a token contract. Returns (txs, max_block_seen)."""
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
                logger.error("Error fetching page %d for contract %s: %s", page, contract, e)
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

    # ------------------------------------------------------------------
    # iGaming scan helpers
    # ------------------------------------------------------------------

    def _filter_and_extract_igaming(
        self,
        txs: list[dict],
        spec: _BrandSpec,
        token_specs: list[_TokenSpec],
        network: str,
        threshold: float,
    ) -> list[dict]:
        """Filter and normalise iGaming brand outgoing transfers.

        Only keeps rows where from_address == brand wallet (payouts to users).
        Token must match one of our tracked tokens (USDT, USDC, etc.) so we
        can correctly decode the amount.  Applies igaming_threshold_usd (default
        0 — capture all payouts).
        """
        # Build a lookup: contract_address (lower) → _TokenSpec
        token_by_contract: dict[str, _TokenSpec] = {
            t.contract.lower(): t for t in token_specs
        }

        results: list[dict] = []
        brand_addr = spec.wallet_address.lower()
        threshold_dec = Decimal(str(threshold))

        for tx in txs:
            try:
                from_addr = (tx.get("from") or "").lower()
                if from_addr != brand_addr:
                    continue  # only outgoing (brand → user)

                to_addr = (tx.get("to") or "").lower()
                if not to_addr or to_addr == brand_addr:
                    continue

                contract_addr = (tx.get("contractAddress") or "").lower()
                token_spec = token_by_contract.get(contract_addr)
                if token_spec is None:
                    continue  # not a tracked token

                scale = Decimal(10) ** token_spec.decimals
                amount = Decimal(tx.get("value", "0")) / scale

                if threshold > 0 and amount < threshold_dec:
                    continue

                ts = int(tx.get("timeStamp", 0))
                results.append({
                    "tx_hash": tx.get("hash", ""),
                    "log_index": int(tx.get("logIndex", 0) or 0),
                    "block_number": int(tx.get("blockNumber", 0)),
                    "network": network,
                    "token_id": token_spec.id,
                    "from_address": from_addr,
                    "to_address": to_addr,
                    "amount": amount,
                    "amount_usd": amount,
                    "transferred_at": datetime.fromtimestamp(ts, tz=timezone.utc),
                    "scan_mode": "igaming",
                    "igaming_brand_id": spec.id,
                })
            except Exception as e:
                logger.warning("Skipping iGaming tx due to parse error: %s", e)
        return results

    @staticmethod
    def _apply_igaming_quality_gate(
        rows: list[dict],
        *,
        blocklist: set[str],
        known_contracts: set[str],
    ) -> tuple[list[dict], dict[str, int]]:
        """Light quality gate for iGaming rows.

        Applies: blocklist + contract-recipient check only.
        Skips aggregator detection — a user receiving many payouts from a
        brand is a high-value target, not noise.
        """
        kept: list[dict] = []
        counts = {"blocklist": 0, "contract": 0}
        for r in rows:
            to_addr = r["to_address"]
            from_addr = r["from_address"]
            if to_addr in blocklist or from_addr in blocklist:
                counts["blocklist"] += 1
                continue
            if to_addr in known_contracts:
                counts["contract"] += 1
                continue
            kept.append(r)
        return kept, counts

    async def _fetch_all_pages_for_address(
        self, wallet_address: str, start_block: int, chain_id: int
    ) -> tuple[list[dict], int]:
        """Fetch up to MAX_PAGES_PER_RUN pages of ERC-20 transfers involving a wallet.

        Returns (txs, max_block_seen).
        """
        all_txs: list[dict] = []
        max_block = start_block

        for page in range(1, MAX_PAGES_PER_RUN + 1):
            try:
                txs = await self._etherscan.get_address_token_transfers(
                    wallet_address=wallet_address,
                    start_block=start_block,
                    page=page,
                    offset=self._page_size,
                    chain_id=chain_id,
                )
            except Exception as e:
                logger.error("Error fetching page %d for brand wallet %s: %s", page, wallet_address, e)
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

    # ------------------------------------------------------------------
    # Batch insert helper (shared between modes)
    # ------------------------------------------------------------------

    @staticmethod
    async def _batch_insert(session: AsyncSession, rows: list[dict]) -> int:
        """Insert rows in 3000-row chunks; returns count of newly inserted rows.

        PostgreSQL has a hard limit of 32 767 bind parameters per statement.
        Each row has 11 columns, so cap each batch at 2900 rows to stay safe.
        """
        inserted = 0
        BATCH_SIZE = 2900
        for i in range(0, len(rows), BATCH_SIZE):
            chunk = rows[i : i + BATCH_SIZE]
            stmt = (
                pg_insert(AirdropTransaction.__table__)
                .values(chunk)
                .on_conflict_do_nothing(constraint="uq_airdrop_tx_hash_log_token")
            )
            res = await session.execute(stmt)
            inserted += res.rowcount or 0
        return inserted

    # ------------------------------------------------------------------
    # Main monitor entry point
    # ------------------------------------------------------------------

    async def run_monitor(
        self,
        scan_mode: str = "standard",
        start_block_override: Optional[int] = None,
    ) -> MonitorRunResult:
        """Run a monitor pass for the requested scan mode(s).

        scan_mode: "standard" | "igaming" | "both"
        """
        scan_mode = (scan_mode or "standard").strip().lower()
        if scan_mode not in VALID_SCAN_MODES:
            scan_mode = "standard"

        now_str = datetime.now(tz=timezone.utc).isoformat()
        errors: list[str] = []
        tokens_scanned: list[str] = []
        brands_scanned: list[str] = []
        blocks_scanned_per_token: dict[str, dict] = {}
        blocks_scanned_per_brand: dict[str, dict] = {}
        new_count = 0

        quality_drop_totals: dict[str, int] = {
            "blocklist": 0, "from_blocklist": 0, "mint": 0,
            "contract": 0, "self_tx": 0, "aggregator": 0, "from_aggregator": 0,
        }
        quality_enrich_summary: dict[str, int] = {}
        quality_prune_summary: dict[str, int] = {}

        # 1) Load config, tokens, brands, and quality settings in one session.
        async with async_session_factory() as session:
            active_network = await get_active_network(session)
            chain_id = chain_id_for(active_network)

            threshold = await self._load_threshold(session)
            igaming_threshold = await self._load_igaming_threshold(session)
            qs = await wallet_quality.load_quality_settings(session)

            tokens: list[AirdropToken] = []
            brands: list[IgamingBrand] = []

            if scan_mode in ("standard", "both"):
                tokens = await self._load_active_tokens(session)

            if scan_mode in ("igaming", "both"):
                brands = await self._load_active_brands(session)

            if not tokens and not brands:
                logger.warning("No active tokens or brands configured for scan_mode=%s", scan_mode)
                total = await session.scalar(select(func.count()).select_from(AirdropTransaction)) or 0
                return MonitorRunResult(
                    tokens_scanned=[],
                    brands_scanned=[],
                    new_transfers_inserted=0,
                    total_transfers_stored=int(total),
                    blocks_scanned_per_token={},
                    blocks_scanned_per_brand={},
                    run_timestamp=now_str,
                    errors=["No active tokens or brands configured"],
                )

            token_specs = [
                _TokenSpec(
                    id=t.id,
                    symbol=t.symbol,
                    contract=t.contract_address,
                    decimals=t.decimals,
                    start_block=(
                        start_block_override
                        if start_block_override is not None
                        else (t.last_scanned_block or 0)
                    ),
                )
                for t in tokens
            ]
            brand_specs = [
                _BrandSpec(
                    id=b.id,
                    name=b.name,
                    wallet_address=b.wallet_address,
                    start_block=(
                        start_block_override
                        if start_block_override is not None
                        else (b.last_scanned_block or 0)
                    ),
                )
                for b in brands
            ]

        quality_enabled = qs.quality_filter_enabled

        # 2) Fetch concurrently (no DB session held).
        fetch_coros = []
        fetch_labels: list[tuple[str, str]] = []  # (kind, identifier)

        for spec in token_specs:
            fetch_coros.append(self._fetch_all_pages(spec.contract, spec.start_block, chain_id))
            fetch_labels.append(("token", spec.symbol))

        for spec in brand_specs:
            fetch_coros.append(
                self._fetch_all_pages_for_address(spec.wallet_address, spec.start_block, chain_id)
            )
            fetch_labels.append(("brand", spec.name))

        fetch_results = await asyncio.gather(*fetch_coros, return_exceptions=True)

        # Split results back into token and brand buckets.
        token_results = fetch_results[: len(token_specs)]
        brand_results = fetch_results[len(token_specs) :]

        # Build token contract set (for mint-event detection in standard mode).
        token_contracts: set[str] = {spec.contract.lower() for spec in token_specs}

        # 3) Persist in one transaction.
        async with async_session_factory() as session:
            # Load Gate A inputs once (blocklist + known contracts for active_network).
            blocklist: set[str] = set()
            known_contracts: set[str] = set()
            if quality_enabled:
                blocklist = await wallet_quality.load_blocklist(session, active_network)
                known_contracts = await wallet_quality.load_known_contracts(session, active_network)

            # --- Standard scan rows ---
            all_standard_rows: list[dict] = []
            for spec, result in zip(token_specs, token_results):
                tokens_scanned.append(spec.symbol)

                if isinstance(result, Exception):
                    msg = f"{spec.symbol}: {type(result).__name__}: {result}"
                    logger.error(msg)
                    errors.append(msg)
                    blocks_scanned_per_token[spec.symbol] = {
                        "from": spec.start_block, "to": spec.start_block, "fetched": 0,
                    }
                    continue

                txs, max_block = result
                blocks_scanned_per_token[spec.symbol] = {
                    "from": spec.start_block, "to": max_block, "fetched": len(txs),
                }

                rows = self._filter_and_extract(txs, spec, active_network, threshold)

                if quality_enabled and rows:
                    rows, drop_counts = self._apply_quality_gate_a(
                        rows,
                        blocklist=blocklist,
                        known_contracts=known_contracts,
                        aggregator_threshold=qs.quality_per_run_aggregator_drop_threshold,
                        token_contracts=token_contracts,
                        from_aggregator_threshold=qs.quality_from_aggregator_drop_threshold,
                    )
                    for k, v in drop_counts.items():
                        quality_drop_totals[k] += v
                    blocks_scanned_per_token[spec.symbol]["quality_dropped"] = sum(drop_counts.values())

                all_standard_rows.extend(rows)

                if start_block_override is None and max_block > spec.start_block:
                    await session.execute(
                        AirdropToken.__table__.update()
                        .where(AirdropToken.id == spec.id)
                        .values(last_scanned_block=max_block + 1)
                    )

            if all_standard_rows:
                new_count += await self._batch_insert(session, all_standard_rows)

            # --- iGaming scan rows ---
            all_igaming_rows: list[dict] = []
            for spec, result in zip(brand_specs, brand_results):
                brands_scanned.append(spec.name)

                if isinstance(result, Exception):
                    msg = f"{spec.name}: {type(result).__name__}: {result}"
                    logger.error(msg)
                    errors.append(msg)
                    blocks_scanned_per_brand[spec.name] = {
                        "from": spec.start_block, "to": spec.start_block, "fetched": 0,
                    }
                    continue

                txs, max_block = result
                blocks_scanned_per_brand[spec.name] = {
                    "from": spec.start_block, "to": max_block, "fetched": len(txs),
                }

                rows = self._filter_and_extract_igaming(
                    txs, spec, token_specs, active_network, igaming_threshold
                )

                if quality_enabled and rows:
                    rows, drop_counts = self._apply_igaming_quality_gate(
                        rows,
                        blocklist=blocklist,
                        known_contracts=known_contracts,
                    )
                    blocks_scanned_per_brand[spec.name]["quality_dropped"] = sum(drop_counts.values())

                all_igaming_rows.extend(rows)

                if start_block_override is None and max_block > spec.start_block:
                    await session.execute(
                        IgamingBrand.__table__.update()
                        .where(IgamingBrand.id == spec.id)
                        .values(last_scanned_block=max_block + 1)
                    )

            if all_igaming_rows:
                new_count += await self._batch_insert(session, all_igaming_rows)

            await session.commit()

        # 4) Gate B: enrich + prune (standard-mode recipients only).
        if quality_enabled and token_specs:
            async with async_session_factory() as session:
                if qs.quality_contract_check_enabled:
                    unknown_addrs = await session.scalars(
                        select(AirdropTransaction.to_address)
                        .where(AirdropTransaction.network == active_network)
                        .where(AirdropTransaction.scan_mode == "standard")
                        .where(
                            AirdropTransaction.to_address.notin_(
                                select(WalletContractCache.address).where(
                                    WalletContractCache.network == active_network
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
                                session, addrs, network=active_network,
                                concurrency=qs.quality_contract_check_concurrency,
                            )
                            for k, v in summary.items():
                                quality_enrich_summary[k] = quality_enrich_summary.get(k, 0) + v
                        except Exception as e:
                            msg = f"contract-enrich {active_network}: {type(e).__name__}: {e}"
                            logger.warning(msg)
                            errors.append(msg)

                try:
                    contract_pruned = await wallet_quality.prune_contract_recipients(
                        session, active_network
                    )
                    prune = await wallet_quality.prune_low_quality(
                        session, active_network,
                        max_inbound_count=qs.quality_max_inbound_count,
                        max_distinct_senders=qs.quality_max_distinct_senders,
                        dormant_singleton_days=qs.quality_dormant_singleton_days,
                    )
                    cross_pruned = await wallet_quality.prune_cross_token_aggregators(
                        session, active_network, qs.quality_cross_token_aggregator_threshold
                    )
                    sender_pruned = await wallet_quality.prune_high_frequency_senders(
                        session, active_network, qs.quality_max_outbound_count
                    )
                    prune["contract"] = contract_pruned
                    prune["cross_token"] = cross_pruned
                    prune["high_freq_sender"] = sender_pruned
                    prune["rows_deleted"] = (
                        prune.get("rows_deleted", 0) + contract_pruned + cross_pruned + sender_pruned
                    )
                    for k, v in prune.items():
                        quality_prune_summary[k] = quality_prune_summary.get(k, 0) + v
                except Exception as e:
                    msg = f"prune {active_network}: {type(e).__name__}: {e}"
                    logger.warning(msg)
                    errors.append(msg)

        async with async_session_factory() as session:
            total = await session.scalar(select(func.count()).select_from(AirdropTransaction)) or 0

        logger.info(
            "Monitor run complete. mode=%s inserted=%s total=%s gateA_drops=%s enrich=%s prune=%s",
            scan_mode, new_count, total, quality_drop_totals, quality_enrich_summary, quality_prune_summary,
        )

        if quality_enabled and (tokens_scanned or brands_scanned):
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
            brands_scanned=brands_scanned,
            new_transfers_inserted=new_count,
            total_transfers_stored=int(total),
            blocks_scanned_per_token=blocks_scanned_per_token,
            blocks_scanned_per_brand=blocks_scanned_per_brand,
            run_timestamp=now_str,
            errors=errors,
        )

    async def get_status(self) -> AirdropStatusResponse:
        async with async_session_factory() as session:
            tokens = await session.scalars(select(AirdropToken).order_by(AirdropToken.symbol))
            last_block_per_token: dict[str, int] = {
                t.symbol: int(t.last_scanned_block or 0) for t in tokens.all()
            }

            brands = await session.scalars(select(IgamingBrand).order_by(IgamingBrand.name))
            last_block_per_brand: dict[str, int] = {
                b.name: int(b.last_scanned_block or 0) for b in brands.all()
            }

            total = await session.scalar(select(func.count()).select_from(AirdropTransaction)) or 0
            last_run = await session.scalar(select(func.max(AirdropTransaction.created_at)))

            # Breakdown: how many transactions came from each mode
            std_count = await session.scalar(
                select(func.count()).select_from(AirdropTransaction)
                .where(AirdropTransaction.scan_mode == "standard")
            ) or 0
            ig_count = await session.scalar(
                select(func.count()).select_from(AirdropTransaction)
                .where(AirdropTransaction.scan_mode == "igaming")
            ) or 0

        return AirdropStatusResponse(
            last_run_timestamp=last_run.isoformat() if last_run else None,
            last_block_per_token=last_block_per_token,
            last_block_per_brand=last_block_per_brand,
            total_transfers=int(total),
            scan_mode_breakdown={"standard": int(std_count), "igaming": int(ig_count)},
        )


# Module-level singleton
monitor_service = AirdropMonitorService()
