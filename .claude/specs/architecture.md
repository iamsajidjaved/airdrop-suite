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
        └─────┬──┬────┘
              │  │
       ┌──────▼  ▼─────────┐
       │  services/        │
       │  etherscan        │──► api.etherscan.io/v2
       │  trongrid         │──► api.trongrid.io
       │  airdrop_monitor  │──► etherscan + Postgres
       └────────┬──────────┘
                │
          ┌─────▼─────┐
          │ Postgres  │  (Neon in prod, async via asyncpg)
          │  airdrop_*│
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

### 2. Airdrop monitoring — stateful, scheduled

```
POST /api/airdrop/monitor/run        ← also: scripts/monitor_airdrops.py
  └─ services/airdrop_monitor.run_monitor
       1. Open session, load active tokens + threshold
       2. asyncio.gather → fetch contract transfers per token
                           (paginated, up to MAX_PAGES_PER_RUN=10)
       3. Open session, for each token:
            - filter txs by amount ≥ threshold
            - INSERT … ON CONFLICT DO NOTHING (chunks of 3000)
            - update last_scanned_block (unless start_block_override was set)
       4. Return MonitorRunResult (counts, errors, blocks scanned)
```

Reads come from `routers/airdrop.py` (token CRUD, transaction list, status, config). Both write paths and read paths share `backend/db_models.py` ORM definitions.

## Why this split

- Wallet exploration scales by upstream rate limits — no DB pressure, easy to cache later if needed.
- Airdrop monitoring is CPU-light but I/O-heavy and *needs* persistence (resume-from-last-block, dedup across runs). Putting it in the same app makes deployment one process; nothing about the code prevents extracting it to a worker later.

## Notable design choices

- **Async everywhere** at runtime. Sync code (psycopg2) exists only for Alembic.
- **Schema is owned by Alembic**, not by `Base.metadata.create_all`. Safer for production (Neon).
- **Dedup is done in the DB**, not in Python: a unique constraint on `(tx_hash, log_index, token_id)` plus `ON CONFLICT DO NOTHING` makes monitor reruns idempotent.
- **Frontend has no build pipeline** — every static file is served as-is with `Cache-Control: no-store` so browsers always pick up fresh JS/CSS in dev.
- **CORS is wide open** (`allow_origins=["*"]`) — fine for dev and the current single-origin deploy on Render. Tighten before exposing the API to third parties.

## File-level entry points

| Concern | File:line |
| --- | --- |
| FastAPI app + routes registration | `backend/main.py:21-38` |
| Static file mount + cache headers | `backend/main.py:41-53` |
| Page routes (HTML) | `backend/main.py:56-80` |
| Wallet endpoint | `backend/routers/transactions.py:54` |
| Airdrop endpoints | `backend/routers/airdrop.py` (whole file) |
| Monitor algorithm | `backend/services/airdrop_monitor.py:132-240` |
| ORM models | `backend/db_models.py` |
| DB engine + session | `backend/db.py` |
| Settings | `backend/config.py` |
