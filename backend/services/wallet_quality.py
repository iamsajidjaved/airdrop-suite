"""Wallet quality filtering for the airdrop monitor.

Two responsibilities:

1. **Gate A (pre-insert)** — provide cheap address sets used by the monitor to
   drop obvious noise (blocklist, known contracts, self-transfers, in-batch
   aggregators) *before* rows hit the DB.
2. **Gate B (post-insert)** — enrich newly-seen addresses with `eth_getCode`
   (cache result), then prune the `airdrop_transactions` table of rows whose
   `to_address` is a contract or aggregate-scores as low quality.

The goal is that only quality recipient candidates ever remain in the DB, so
campaigns built from `airdrop_transactions` automatically inherit the filter.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.db_models import (
    AirdropConfig,
    AirdropTransaction,
    DistributionWallet,
    QualityAddressBlocklist,
    WalletContractCache,
)

logger = logging.getLogger(__name__)


async def load_quality_settings(session: AsyncSession) -> "QualityFilterSettings":
    """Load quality filter settings from airdrop_config, falling back to env vars."""
    from backend.models import QualityFilterSettings

    _KEYS = [
        "quality_filter_enabled",
        "quality_contract_check_enabled",
        "quality_contract_check_concurrency",
        "quality_per_run_aggregator_drop_threshold",
        "quality_from_aggregator_drop_threshold",
        "quality_max_inbound_count",
        "quality_max_distinct_senders",
        "quality_max_outbound_count",
        "quality_dormant_singleton_days",
        "quality_cross_token_aggregator_threshold",
    ]
    rows = await session.execute(
        select(AirdropConfig.key, AirdropConfig.value).where(
            AirdropConfig.key.in_(_KEYS)
        )
    )
    db: dict[str, str] = {k: v for k, v in rows.all()}

    def _bool(key: str, fallback: bool) -> bool:
        raw = db.get(key)
        return fallback if raw is None else raw.strip().lower() in ("1", "true", "yes")

    def _int(key: str, fallback: int) -> int:
        raw = db.get(key)
        if raw is None:
            return fallback
        try:
            return int(raw)
        except (ValueError, TypeError):
            return fallback

    return QualityFilterSettings(
        quality_filter_enabled=_bool("quality_filter_enabled", settings.quality_filter_enabled),
        quality_contract_check_enabled=_bool("quality_contract_check_enabled", settings.quality_contract_check_enabled),
        quality_contract_check_concurrency=_int("quality_contract_check_concurrency", settings.quality_contract_check_concurrency),
        quality_per_run_aggregator_drop_threshold=_int("quality_per_run_aggregator_drop_threshold", settings.quality_per_run_aggregator_drop_threshold),
        quality_from_aggregator_drop_threshold=_int("quality_from_aggregator_drop_threshold", settings.quality_from_aggregator_drop_threshold),
        quality_max_inbound_count=_int("quality_max_inbound_count", settings.quality_max_inbound_count),
        quality_max_distinct_senders=_int("quality_max_distinct_senders", settings.quality_max_distinct_senders),
        quality_max_outbound_count=_int("quality_max_outbound_count", settings.quality_max_outbound_count),
        quality_dormant_singleton_days=_int("quality_dormant_singleton_days", settings.quality_dormant_singleton_days),
        quality_cross_token_aggregator_threshold=_int("quality_cross_token_aggregator_threshold", 0),
    )


# ---------- Gate A loaders (cheap, run once per monitor pass) ----------

async def load_blocklist(session: AsyncSession, network: str) -> set[str]:
    """Static blocklist + our own distribution wallets, all lowercase."""
    rows = await session.scalars(
        select(QualityAddressBlocklist.address).where(
            QualityAddressBlocklist.network == network
        )
    )
    out = {a.lower() for a in rows.all()}

    # Never airdrop to ourselves.
    own = await session.scalars(select(DistributionWallet.address))
    out.update(a.lower() for a in own.all())
    return out


async def load_known_contracts(session: AsyncSession, network: str) -> set[str]:
    """Addresses previously confirmed to be smart contracts. Cached forever."""
    rows = await session.scalars(
        select(WalletContractCache.address).where(
            WalletContractCache.network == network,
            WalletContractCache.is_contract.is_(True),
        )
    )
    return {a.lower() for a in rows.all()}


def in_batch_aggregator_set(
    rows: list[dict], threshold: int
) -> set[str]:
    """Return to_addresses that exceed `threshold` rows in the current batch."""
    if threshold <= 0:
        return set()
    counter: Counter[str] = Counter(r["to_address"] for r in rows)
    return {addr for addr, c in counter.items() if c > threshold}


def in_batch_from_aggregator_set(rows: list[dict], threshold: int) -> set[str]:
    """Return from_addresses that sent to > threshold distinct to_addresses in this batch.

    Catches faucets and bot distributors: one sender → many distinct recipients.
    """
    if threshold <= 0:
        return set()
    from_to: dict[str, set[str]] = {}
    for r in rows:
        from_to.setdefault(r["from_address"], set()).add(r["to_address"])
    return {fa for fa, tas in from_to.items() if len(tas) > threshold}


# ---------- Gate B: contract enrichment ----------

async def enrich_contracts(
    session: AsyncSession,
    addresses: Iterable[str],
    network: str = "ethereum",
    *,
    concurrency: int | None = None,
) -> dict[str, int]:
    """Call `eth_getCode` for any address not yet in the cache, upsert results.

    Returns counts: {"checked": N, "contracts": M, "eoas": K, "skipped": S}.
    Safe to call with an empty iterable. Silently no-ops if web3 is unavailable.
    """
    addrs = sorted({a.lower() for a in addresses if a})
    if not addrs:
        return {"checked": 0, "contracts": 0, "eoas": 0, "skipped": 0}

    # Filter out anything already cached.
    existing_rows = await session.scalars(
        select(WalletContractCache.address).where(
            WalletContractCache.network == network,
            WalletContractCache.address.in_(addrs),
        )
    )
    cached = {a.lower() for a in existing_rows.all()}
    todo = [a for a in addrs if a not in cached]
    if not todo:
        return {"checked": 0, "contracts": 0, "eoas": 0, "skipped": len(addrs)}

    try:
        from backend.services.web3_client import Web3Unavailable, get_web3, to_checksum  # lazy
        w3 = get_web3()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Quality: skipping contract enrichment (%s): %s", type(e).__name__, e
        )
        return {"checked": 0, "contracts": 0, "eoas": 0, "skipped": len(addrs)}

    sem = asyncio.Semaphore(concurrency or settings.quality_contract_check_concurrency)
    results: dict[str, bool] = {}

    async def _check(addr: str) -> None:
        async with sem:
            try:
                code = await w3.eth.get_code(to_checksum(addr))
                results[addr] = bool(code) and code != b"\x00" and len(code) > 0
            except Exception as e:  # noqa: BLE001
                logger.debug("eth_getCode failed for %s: %s", addr, e)

    await asyncio.gather(*(_check(a) for a in todo))

    if not results:
        return {"checked": 0, "contracts": 0, "eoas": 0, "skipped": len(addrs)}

    rows = [
        {
            "network": network,
            "address": addr,
            "is_contract": is_contract,
            "checked_at": datetime.now(tz=timezone.utc),
        }
        for addr, is_contract in results.items()
    ]
    # Upsert (rare conflict if two concurrent passes raced).
    BATCH = 1000
    for i in range(0, len(rows), BATCH):
        chunk = rows[i : i + BATCH]
        stmt = (
            pg_insert(WalletContractCache.__table__)
            .values(chunk)
            .on_conflict_do_nothing(constraint="uq_wallet_contract_cache_network_address")
        )
        await session.execute(stmt)
    await session.commit()

    contracts = sum(1 for v in results.values() if v)
    return {
        "checked": len(results),
        "contracts": contracts,
        "eoas": len(results) - contracts,
        "skipped": len(addrs) - len(results),
    }


# ---------- Gate B: aggregate prune ----------

async def prune_contract_recipients(session: AsyncSession, network: str) -> int:
    """Delete airdrop_transactions whose to_address is a known contract."""
    sub = (
        select(WalletContractCache.address)
        .where(
            WalletContractCache.network == network,
            WalletContractCache.is_contract.is_(True),
        )
        .scalar_subquery()
    )
    res = await session.execute(
        delete(AirdropTransaction)
        .where(AirdropTransaction.network == network)
        .where(AirdropTransaction.to_address.in_(sub))
    )
    await session.commit()
    return res.rowcount or 0


async def prune_low_quality(
    session: AsyncSession,
    network: str,
    *,
    max_inbound_count: int | None = None,
    max_distinct_senders: int | None = None,
    dormant_singleton_days: int | None = None,
) -> dict[str, int]:
    """Delete rows whose `to_address` aggregates to low-quality.

    Rules (any match → delete all rows for that to_address):
      * inbound_count > max_inbound_count          (exchange-like)
      * distinct_senders > max_distinct_senders    (hot wallet)
      * exactly 1 inbound and last_seen older than dormant cutoff
    Returns counts: {"aggregator": A, "dormant_singleton": D, "rows_deleted": R}.
    Keyword args override env-var defaults so the caller can pass DB-loaded values.
    """
    _max_inbound = max_inbound_count if max_inbound_count is not None else settings.quality_max_inbound_count
    _max_senders = max_distinct_senders if max_distinct_senders is not None else settings.quality_max_distinct_senders
    _dormant_days = dormant_singleton_days if dormant_singleton_days is not None else settings.quality_dormant_singleton_days

    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_dormant_days)

    # Aggregator delete
    agg_sql = text(
        """
        WITH bad AS (
            SELECT to_address
            FROM airdrop_transactions
            WHERE network = :network
            GROUP BY to_address
            HAVING COUNT(*) > :max_inbound
                OR COUNT(DISTINCT from_address) > :max_senders
        )
        DELETE FROM airdrop_transactions t
        USING bad
        WHERE t.network = :network
          AND t.to_address = bad.to_address
        """
    )
    agg_res = await session.execute(
        agg_sql,
        {
            "network": network,
            "max_inbound": _max_inbound,
            "max_senders": _max_senders,
        },
    )
    agg_deleted = agg_res.rowcount or 0

    # Dormant singleton delete
    dorm_sql = text(
        """
        WITH bad AS (
            SELECT to_address
            FROM airdrop_transactions
            WHERE network = :network
            GROUP BY to_address
            HAVING COUNT(*) = 1 AND MAX(transferred_at) < :cutoff
        )
        DELETE FROM airdrop_transactions t
        USING bad
        WHERE t.network = :network
          AND t.to_address = bad.to_address
        """
    )
    dorm_res = await session.execute(dorm_sql, {"network": network, "cutoff": cutoff})
    dorm_deleted = dorm_res.rowcount or 0

    await session.commit()
    return {
        "aggregator": agg_deleted,
        "dormant_singleton": dorm_deleted,
        "rows_deleted": agg_deleted + dorm_deleted,
    }


async def prune_cross_token_aggregators(
    session: AsyncSession, network: str, threshold: int
) -> int:
    """Delete airdrop_transactions where to_address appears in > threshold distinct token IDs.

    Catches wallets (DEX routers, bridges) that collect across many token contracts.
    threshold=0 disables the check entirely.
    Returns number of rows deleted.
    """
    if threshold <= 0:
        return 0
    sql = text(
        """
        WITH bad AS (
            SELECT to_address
            FROM airdrop_transactions
            WHERE network = :network
            GROUP BY to_address
            HAVING COUNT(DISTINCT token_id) > :threshold
        )
        DELETE FROM airdrop_transactions t
        USING bad
        WHERE t.network = :network
          AND t.to_address = bad.to_address
        """
    )
    res = await session.execute(sql, {"network": network, "threshold": threshold})
    await session.commit()
    return res.rowcount or 0


async def prune_high_frequency_senders(
    session: AsyncSession,
    network: str,
    max_distinct_recipients: int,
) -> int:
    """Delete transactions whose from_address sent to too many distinct recipients.

    Catches faucets and batch-distribution bots that operate across multiple runs.
    """
    if max_distinct_recipients <= 0:
        return 0
    sql = text(
        """
        WITH bad AS (
            SELECT from_address
            FROM airdrop_transactions
            WHERE network = :network
            GROUP BY from_address
            HAVING COUNT(DISTINCT to_address) > :threshold
        )
        DELETE FROM airdrop_transactions t
        USING bad
        WHERE t.network = :network
          AND t.from_address = bad.from_address
        """
    )
    res = await session.execute(sql, {"network": network, "threshold": max_distinct_recipients})
    await session.commit()
    return res.rowcount or 0


# ---------- Blocklist admin helpers ----------

async def add_to_blocklist(
    session: AsyncSession,
    *,
    address: str,
    reason: str,
    network: str = "ethereum",
    source: str = "manual",
) -> None:
    stmt = (
        pg_insert(QualityAddressBlocklist.__table__)
        .values(
            network=network,
            address=address.lower(),
            reason=reason,
            source=source,
        )
        .on_conflict_do_nothing(constraint="uq_quality_blocklist_network_address")
    )
    await session.execute(stmt)
    await session.commit()


async def remove_from_blocklist(
    session: AsyncSession, *, address: str, network: str = "ethereum"
) -> int:
    res = await session.execute(
        delete(QualityAddressBlocklist).where(
            QualityAddressBlocklist.network == network,
            QualityAddressBlocklist.address == address.lower(),
        )
    )
    await session.commit()
    return res.rowcount or 0
