# Airdrop monitor

Stateful background scanner that walks ERC-20 token contracts for transfers ≥ a USD threshold and persists qualifying ones to Postgres. Source: `backend/services/airdrop_monitor.py`. Triggered manually (web or CLI); no built-in scheduler.

## Purpose

Stablecoin airdrop / large-transfer tracking. The monitor watches a small set of token contracts (USDT, USDC by default) and records every transfer above the configured USD threshold (default `$500`) with sender, receiver, amount, block, and timestamp.

## Entry points

| Trigger | Path |
| --- | --- |
| HTTP | `POST /api/airdrop/monitor/run` (optional `?start_block_override=N`) |
| CLI | `uv run python scripts/monitor_airdrops.py` |

Both call the same `AirdropMonitorService.run_monitor` method, so behavior is identical.

## Algorithm

```
load active tokens + threshold from DB
for each token:                                    ┐
    fetch contract transfers from last_scanned_block,│ asyncio.gather
    paginated, up to MAX_PAGES_PER_RUN (=10)         │ across tokens
                                                    ┘
in a single DB transaction:
    for each (token, fetched_txs):
        keep tx where amount ≥ threshold
        bulk INSERT … ON CONFLICT DO NOTHING (chunks of 3000)
        if not start_block_override:
            UPDATE last_scanned_block = max_block_seen + 1
    commit
return MonitorRunResult
```

Concrete reference points:
- Active token + threshold load: `backend/services/airdrop_monitor.py:60-65, 49-58`
- Pagination: `_fetch_all_pages` at `:97-130`. Stops early if a page returns < `page_size` rows or on error.
- Filtering & extraction: `_filter_and_extract` at `:66-95`. Compares decimal-scaled amount vs threshold; sets `amount_usd = amount` (stablecoin assumption — fine for USDT/USDC, would need a price feed for non-stables).
- Persistence: chunked `pg_insert(...).on_conflict_do_nothing(constraint="uq_airdrop_tx_hash_log_token")` at `:204-217`.
- Cursor update: `:219-224`. Skipped when `start_block_override` is set so backfills don't disturb live state.

## Configuration

| What | Where | Default |
| --- | --- | --- |
| Active tokens (symbol, contract, decimals) | `airdrop_tokens` table | seeded with USDT, USDC by `0002_seed_defaults.py` |
| USD threshold | `airdrop_config` row keyed `min_threshold_usd` | `500.0` |
| Page size for Etherscan | `settings.airdrop_page_size` (env `AIRDROP_PAGE_SIZE`) | `1000` |
| Max pages per run per token | `MAX_PAGES_PER_RUN` constant | `10` |
| Bulk insert chunk size | `BATCH_SIZE` constant in `run_monitor` | `3000` (kept under PostgreSQL's 32 767 bind-params limit at 10 cols/row) |

The two `MAX_PAGES_PER_RUN` × `airdrop_page_size` together cap one run per token at 10 000 transfers. If a contract is busier than that between runs, a single trigger won't catch up — schedule more frequent runs or temporarily raise the limits.

## Idempotency

The unique constraint `(tx_hash, log_index, token_id)` on `airdrop_transactions` plus `ON CONFLICT DO NOTHING` makes reruns safe. Re-running a successful pass produces zero new rows.

## Failure modes

- **Etherscan errors per page** are caught in `_fetch_all_pages` and break the page loop early — partial results for that token still get persisted, the cursor advances to the highest block actually seen, and the error is recorded in `MonitorRunResult.errors`.
- **Per-token exceptions** raised by `asyncio.gather(..., return_exceptions=True)` are surfaced in `errors[]` with the symbol prefix; the cursor for that token is **not** advanced (`:189-194`).
- **Parse errors per row** are logged and the row is skipped (`:93-95`).
- **DB conflicts** are silently absorbed by `ON CONFLICT DO NOTHING` — that's by design.

## Manual operations

- **Backfill from a specific block without disturbing the cursor:**
  `POST /api/airdrop/monitor/run?start_block_override=18000000`
- **Reset a token's cursor:** `PATCH /api/airdrop/tokens/{id}` with `{ "last_scanned_block": <block> }` (see `backend/models.py:115`).
- **Disable a token without deleting history:** `PATCH /api/airdrop/tokens/{id}` with `{ "is_active": false }`. (DELETE is blocked when transactions exist; see `.claude/specs/api.md`.)
- **Change the threshold:** `PUT /api/airdrop/config` with `{ "min_threshold_usd": 1000 }`. Applies to *future* runs only — historical data is not retroactively filtered.
- **Run from the CLI:** `uv run python scripts/monitor_airdrops.py`.

## Extending

- **New stablecoin:** add a row via `POST /api/airdrop/tokens` (or admin UI). No code change.
- **Non-stablecoin token:** the current `amount_usd = amount` shortcut breaks. Add a price-lookup step in `_filter_and_extract` and adjust the threshold check accordingly.
- **Non-Ethereum network:** today the monitor only calls `EtherscanService.get_contract_token_transfers`. Adding Tron-side monitoring would require a parallel `services/airdrop_monitor_tron.py` (or a dispatch-by-network branch) plus updating `network`-aware columns already present on the table.
