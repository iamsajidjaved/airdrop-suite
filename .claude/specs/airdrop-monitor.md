# Airdrop monitor

Stateful scanner that captures ERC-20 transfers and stores qualifying ones to Postgres for use as airdrop recipients. Source: `backend/services/airdrop_monitor.py`. Triggered manually via the admin UI or API; no built-in scheduler.

## Purpose

Dual-mode audience acquisition:

| Mode | What it scans | Who it captures |
| --- | --- | --- |
| **Standard** | ERC-20 token contract(s) for ALL transfers ≥ USD threshold | High-value stablecoin users |
| **iGaming** | OUTGOING transfers FROM configured brand wallets | Verified users of competitor iGaming platforms |

Both modes write to `airdrop_transactions` with a `scan_mode` tag. The distribution system sees all recipients uniformly.

## Entry point

| Trigger | How |
| --- | --- |
| HTTP | `POST /api/airdrop/monitor/run?scan_mode=standard\|igaming\|both` |
| CLI | `uv run python scripts/monitor_airdrops.py` |

Both call the same `AirdropMonitorService.run_monitor(scan_mode)` method.

## Algorithm

```
1. Read active_network from airdrop_config (one async DB read)
   chain_id = chain_id_for(active_network)

if scan_mode in {"standard", "both"}:
2a. Load active tokens + min_threshold_usd from DB
3a. asyncio.gather → get_contract_token_transfers per token
    (paginated by last_scanned_block, up to MAX_PAGES_PER_RUN pages)
4a. For each token's results:
    - filter rows where amount_usd >= min_threshold_usd
    - Quality Gate A: blocklist check, aggregator detection, contract-recipient check
    - INSERT … ON CONFLICT DO NOTHING (chunks of 2900)
    - UPDATE token.last_scanned_block
5a. Quality Gate B: prune contract recipients from standard-mode results

if scan_mode in {"igaming", "both"}:
2b. Load active brands + igaming_threshold_usd from DB
3b. asyncio.gather → get_address_token_transfers per brand wallet
    (paginated by last_scanned_block, up to MAX_PAGES_PER_RUN pages)
4b. For each brand's results:
    - filter rows where from_address == brand.wallet_address (outgoing only)
    - filter by token.contract_address match (only tracked tokens)
    - filter rows where amount_usd >= igaming_threshold_usd
    - Quality Gate A: blocklist check + contract-recipient check
      (NO aggregator detection — many payouts from same brand = legitimate user)
    - INSERT with scan_mode='igaming', igaming_brand_id=brand.id
    - UPDATE brand.last_scanned_block

return MonitorRunResult
```

Concrete reference points:
- Network resolution: `get_active_network(session)` in `backend/config.py`
- Standard scan: `_run_standard_scan()` in `backend/services/airdrop_monitor.py`
- iGaming scan: `_run_igaming_scan()` in `backend/services/airdrop_monitor.py`
- Outgoing filter: `_filter_and_extract_igaming()` — checks `from_address == brand.wallet_address`
- Pagination (tokens): `_fetch_all_pages(token_spec)` — uses `get_contract_token_transfers`
- Pagination (brands): `_fetch_all_pages_for_address(brand_spec)` — uses `get_address_token_transfers`
- Bulk insert: `_batch_insert()` — chunks at 2900 rows × 11 columns

## Configuration

| What | Where | Default |
| --- | --- | --- |
| Active tokens (symbol, contract, decimals) | `airdrop_tokens` table | seeded with USDT, USDC by `0002_seed_defaults.py` |
| Active brands (name, wallet_address) | `igaming_brands` table | empty; add via API or admin UI |
| Standard USD threshold | `airdrop_config` key `min_threshold_usd` | `500.0` |
| iGaming USD threshold | `airdrop_config` key `igaming_threshold_usd` | `0.0` (capture all brand payouts) |
| Active network | `airdrop_config` key `active_network` | `"ethereum"` |
| Page size for Etherscan | `settings.airdrop_page_size` (env `AIRDROP_PAGE_SIZE`) | `1000` |
| Max pages per run per token/brand | `MAX_PAGES_PER_RUN` constant | `10` |
| Bulk insert chunk size | `BATCH_SIZE` in `_batch_insert` | `2900` (≤ 32 767 bind-params at 11 cols/row) |

## Idempotency

The unique constraint `(tx_hash, log_index, token_id)` on `airdrop_transactions` plus `ON CONFLICT DO NOTHING` makes reruns safe. Re-running a successful pass produces zero new rows.

## Failure modes

- **Etherscan errors per page** are caught and break the page loop early — partial results still get persisted, the cursor advances to the highest block actually seen, and the error is recorded in `MonitorRunResult.errors`.
- **Per-token / per-brand exceptions** from `asyncio.gather(..., return_exceptions=True)` are surfaced in `errors[]`; the cursor for that token/brand is **not** advanced.
- **Parse errors per row** are logged and the row is skipped.
- **DB conflicts** are silently absorbed by `ON CONFLICT DO NOTHING` — that's by design.

## Manual operations

- **Backfill from a specific block without disturbing the cursor:**
  `POST /api/airdrop/monitor/run?scan_mode=both&start_block_override=18000000`
- **Reset a token's cursor:** `PATCH /api/airdrop/tokens/{id}` with `{ "last_scanned_block": <block> }`
- **Reset a brand's cursor:** `PATCH /api/airdrop/brands/{id}` with `{ "last_scanned_block": <block> }`
- **Disable a token without deleting history:** `PATCH /api/airdrop/tokens/{id}` with `{ "is_active": false }`
- **Disable a brand without deleting history:** `PATCH /api/airdrop/brands/{id}` with `{ "is_active": false }`
- **Change the standard threshold:** `PUT /api/airdrop/config` with `{ "min_threshold_usd": 1000 }`. Applies to *future* runs only.
- **Change the active network:** `PUT /api/airdrop/config` with `{ "active_network": "sepolia" }`. Takes effect on next scan.
- **Reset all data:** `POST /api/airdrop/admin/reset` (requires `X-Admin-Token`).

## Extending

- **New stablecoin:** add a row via `POST /api/airdrop/tokens` (or admin UI). No code change.
- **New iGaming brand:** add via `POST /api/airdrop/brands` (or admin UI). No code change.
- **Non-stablecoin token:** the `amount_usd = amount` shortcut breaks. Add a price-lookup step in `_filter_and_extract` and adjust the threshold check accordingly.
- **Non-Ethereum network:** change `active_network` config key and add the network entry to the `NETWORKS` dict in `backend/config.py`. The scanner will use the corresponding Etherscan chain_id automatically.
