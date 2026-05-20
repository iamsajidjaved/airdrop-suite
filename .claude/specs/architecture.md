# Architecture

## High-level

```
                ┌─────────────────────────────┐
                │       Browser (vanilla JS)  │
                │  index / explorer / admin   │
                └──────────────┬──────────────┘
                               │ HTTP (same origin)
                ┌──────────────▼──────────────┐
                │        FastAPI app          │
                │      (backend/main.py)      │
                └─┬───────────────┬───────────┘
                  │               │
        ┌─────────▼──┐     ┌──────▼───────┐
        │ routers/   │     │  StaticFiles │  ← serves frontend/ at /static
        │ transactions│     │   mount      │
        │ + airdrop   │     └──────────────┘
        │ + distribution│
        └─────┬──┬────┘
              │  │
       ┌──────▼  ▼─────────┐
       │  services/        │
       │  etherscan        │──► api.etherscan.io/v2
       │  trongrid         │──► api.trongrid.io
       │  airdrop_monitor  │──► etherscan + Postgres
       │  distribution_    │──► eth JSON-RPC + Postgres
       │    worker         │
       └────────┬──────────┘
                │
          ┌─────▼─────┐
          │ Postgres  │  (Neon in prod, async via asyncpg)
          │  airdrop_*│
          │  igaming_ │
          │  distribu_│
          └───────────┘
```

## Two flows, one app

The codebase has two largely independent feature slices that share a process and a DB pool but nothing else.

### 1. Wallet exploration — stateless, read-through

```
GET /api/transactions/{address}
  └─ routers/transactions.py:get_transactions
       ├─ validate_wallet_address (regex: 0x… for ETH, T… for Tron)
       ├─ if ethereum → services/etherscan.get_all_transactions
       └─ if tron     → services/trongrid.get_all_transactions
                          (with min/max timestamp filter)
       returns TransactionResponse (list + summary)
```

No persistence. Every request hits the upstream API. Date filtering is applied in the router as a safety net even when upstream supports it (`routers/transactions.py:129-135`).

### 2. Airdrop monitoring — stateful, dual-mode scanner

```
POST /api/airdrop/monitor/run?scan_mode=standard|igaming|both
  └─ services/airdrop_monitor.run_monitor(scan_mode)
       1. Read active_network from airdrop_config → derive chain_id (one DB read)
       2. Load tokens + threshold (standard) | load brands (igaming)
       3. asyncio.gather → fetch transfers concurrently:
            standard: get_contract_token_transfers per token contract
            igaming:  get_address_token_transfers per brand wallet (filter outgoing)
       4. Quality Gate A per-row: blocklist + aggregator detection
            (igaming mode skips aggregator detection — many payouts = legit user)
       5. INSERT … ON CONFLICT DO NOTHING (chunks of 2900)
       6. Quality Gate B (standard only): prune contract recipients
       7. Update cursors: last_scanned_block per token | per brand
       8. Return MonitorRunResult (counts, brands_scanned, errors, blocks)
```

#### Standard mode
Scans ERC-20 token contract(s) for ALL transfers ≥ USD threshold. Identifies high-value stablecoin users. Rows stored with `scan_mode='standard'`.

#### iGaming mode
Scans OUTGOING transfers FROM configured brand wallets (e.g. Stake.com hot wallets). Recipients are verified users of competitor iGaming platforms. Rows stored with `scan_mode='igaming'` and `igaming_brand_id` FK.

Both modes write to the same `airdrop_transactions` table. The distribution system sees all recipients uniformly.

### 3. Token distribution — Phase 2

```
Distribution worker (background, opt-in):
  SELECT airdrop_sends WHERE status='pending' FOR UPDATE SKIP LOCKED
  → sign + broadcast ERC-20 transfer
  → poll receipt, update status
```

Sender wallets are stored in `distribution_wallets` with AES-GCM encrypted private keys. The worker is off by default (`DISTRIBUTION_WORKER_ENABLED=false`).

## Network configuration — global, DB-backed

`active_network` in `airdrop_config` (value: `"ethereum"` or `"sepolia"`) is the single source of truth for which blockchain to scan. All services read it at runtime via `get_active_network(session)`. There is no per-token `network` field and no `ETHERSCAN_CHAIN_ID` env var.

```
config.py: chain_id_for(network: str) → int       sync lookup from NETWORKS dict
config.py: get_active_network(session) → str       async DB read, falls back to "ethereum"
```

Changing `active_network` via `PUT /api/airdrop/config` takes effect on the next scan run.

## Why this split

- Wallet exploration scales by upstream rate limits — no DB pressure, easy to cache later if needed.
- Airdrop monitoring is CPU-light but I/O-heavy and *needs* persistence (resume-from-last-block, dedup across runs). Putting it in the same app makes deployment one process; nothing about the code prevents extracting it to a worker later.

## Notable design choices

- **Async everywhere** at runtime. Sync code (psycopg2) exists only for Alembic.
- **Schema is owned by Alembic**, not by `Base.metadata.create_all`. Safer for production (Neon).
- **Dedup is done in the DB**, not in Python: a unique constraint on `(tx_hash, log_index, token_id)` plus `ON CONFLICT DO NOTHING` makes monitor reruns idempotent.
- **iGaming brands are fully dynamic**: add/remove via API or admin UI. No code change needed.
- **Frontend has no build pipeline** — every static file is served as-is with `Cache-Control: no-store` so browsers always pick up fresh JS/CSS in dev.
- **CORS is wide open** (`allow_origins=["*"]`) — fine for dev and the current single-origin deploy. Tighten before exposing the API to third parties.

## File-level entry points

| Concern | File:line |
| --- | --- |
| FastAPI app + routes registration | `backend/main.py:36-56` |
| Static file mount + cache headers | `backend/main.py:58-66` |
| Page routes (HTML) | `backend/main.py:68-127` |
| Wallet endpoint | `backend/routers/transactions.py:54` |
| Airdrop endpoints | `backend/routers/airdrop.py` (whole file) |
| Distribution endpoints | `backend/routers/distribution.py` (whole file) |
| Monitor algorithm | `backend/services/airdrop_monitor.py` |
| Network registry + helpers | `backend/config.py:75-99` |
| ORM models | `backend/db_models.py` |
| DB engine + session | `backend/db.py` |
| Settings | `backend/config.py` |
