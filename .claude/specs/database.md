# Database

PostgreSQL (Neon in production). Async at runtime via `asyncpg`; Alembic uses sync `psycopg2`. Both URLs are derived from a single `DATABASE_URL` env var by `backend/db.py`.

## Connection handling

`backend/db.py` exposes:
- `engine` — async SQLAlchemy engine (`pool_pre_ping=True`, `pool_recycle=300` for Neon's idle disconnects).
- `async_session_factory` — `async_sessionmaker(..., expire_on_commit=False)`.
- `get_session()` — FastAPI dependency yielding an `AsyncSession`. Use `Depends(get_session)` in endpoints.
- `ASYNC_DATABASE_URL`, `ASYNC_CONNECT_ARGS` — used by the engine.
- `SYNC_DATABASE_URL` — picked up by `backend/migrations/env.py` for Alembic.

The URL helpers (`_to_asyncpg_url`, `_to_sync_url`) accept any of `postgres://`, `postgresql://`, `postgresql+psycopg2://`, or `postgresql+asyncpg://`. They strip libpq query params (`sslmode`, `channel_binding`) that asyncpg doesn't understand and translate `sslmode=require` into `connect_args={"ssl": "require"}`.

## Tables

All tables are defined in `backend/db_models.py`. Schema is owned by Alembic — the ORM is never used to create tables.

### `airdrop_tokens`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER PRIMARY KEY` autoincrement | |
| `symbol` | `VARCHAR(32)` `NOT NULL UNIQUE` | uppercased on input |
| `contract_address` | `VARCHAR(64)` `NOT NULL UNIQUE` | lowercased, validated `0x[0-9a-f]{40}` |
| `decimals` | `INTEGER NOT NULL` | |
| `is_active` | `BOOLEAN NOT NULL DEFAULT true` | scanner only picks up active tokens |
| `last_scanned_block` | `BIGINT` nullable | resume cursor; updated after each successful run unless `start_block_override` was passed |
| `created_at`, `updated_at` | `TIMESTAMPTZ NOT NULL` | server defaults `now()` |

Note: the `network` column was dropped in migration `0012_igaming_brands`. Network is now a global config key (`active_network` in `airdrop_config`).

### `airdrop_config`

| Column | Type | Notes |
| --- | --- | --- |
| `key` | `VARCHAR(64) PRIMARY KEY` | |
| `value` | `VARCHAR(255) NOT NULL` | stored as string; cast at read time |
| `updated_at` | `TIMESTAMPTZ NOT NULL` | |

Keys in use:

| Key | Default | Meaning |
| --- | --- | --- |
| `min_threshold_usd` | `500.0` | standard-mode USD floor |
| `active_network` | `"ethereum"` | which chain to scan (`"ethereum"` or `"sepolia"`) |
| `igaming_threshold_usd` | `0.0` | iGaming-mode USD floor (0 = capture all brand payouts) |

### `igaming_brands`

Competitor iGaming platform wallets. Outgoing ERC-20 transfers from these wallets are captured to identify the platform's users.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER PRIMARY KEY` autoincrement | |
| `name` | `VARCHAR(128) NOT NULL` | human label, e.g. "Stake.com" |
| `wallet_address` | `VARCHAR(64) NOT NULL UNIQUE` | the hot wallet address whose outgoing TXs we scan |
| `description` | `VARCHAR(255)` nullable | optional note |
| `is_active` | `BOOLEAN NOT NULL DEFAULT true` | inactive brands are skipped by the scanner |
| `last_scanned_block` | `BIGINT` nullable | per-brand resume cursor (same pattern as tokens) |
| `created_at`, `updated_at` | `TIMESTAMPTZ NOT NULL` | server defaults `now()` |

### `airdrop_transactions`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGINT PRIMARY KEY` autoincrement | |
| `tx_hash` | `VARCHAR(80) NOT NULL` | |
| `log_index` | `INTEGER NOT NULL DEFAULT 0` | needed because a single tx can contain many transfers |
| `block_number` | `BIGINT NOT NULL` | |
| `token_id` | `INTEGER NOT NULL FK → airdrop_tokens.id ON DELETE CASCADE` | |
| `from_address` | `VARCHAR(64) NOT NULL` | lowercased before insert |
| `to_address` | `VARCHAR(64) NOT NULL` | lowercased before insert |
| `amount` | `NUMERIC(38, 18) NOT NULL` | full token precision |
| `amount_usd` | `NUMERIC(38, 8)` nullable | for stablecoins, 1:1 with `amount` |
| `transferred_at` | `TIMESTAMPTZ NOT NULL` | from `timeStamp` of the transfer |
| `scan_mode` | `VARCHAR(20) NOT NULL DEFAULT 'standard'` | `"standard"` or `"igaming"` |
| `igaming_brand_id` | `INTEGER FK → igaming_brands.id ON DELETE SET NULL` nullable | set for iGaming-mode rows |
| `created_at` | `TIMESTAMPTZ NOT NULL` | when the row was written; `get_status` uses `MAX(created_at)` as "last run" |

**Constraints / indexes:**
- `UNIQUE (tx_hash, log_index, token_id)` as `uq_airdrop_tx_hash_log_token` — enables `ON CONFLICT DO NOTHING` for idempotent monitor reruns.
- `INDEX (token_id, block_number)` as `ix_airdrop_tx_token_block`.
- `INDEX (to_address)`, `INDEX (from_address)`, `INDEX (transferred_at)` for the admin transactions list filters.
- `INDEX (scan_mode)` as `ix_airdrop_tx_scan_mode`.

## Migrations

Lives under `backend/migrations/`. Configured by `alembic.ini` (script_location = `backend/migrations`).

```bash
uv run alembic upgrade head                  # apply all pending
uv run alembic revision -m "add foo column"  # new empty migration
uv run alembic downgrade -1                  # roll back one (use with care)
uv run alembic current                       # show current revision
uv run alembic history                       # show migration graph
```

### Existing migrations (in order)

| Revision | What it does |
| --- | --- |
| `0001_initial` | Creates `airdrop_tokens`, `airdrop_config`, `airdrop_transactions` |
| `0002_seed_defaults` | Seeds USDT/USDC + `min_threshold_usd=500.0` from settings |
| `0003` – `0011` | Various incremental additions (distribution tables, campaign/send columns, constraint fixes) |
| `0012_igaming_brands` | Creates `igaming_brands`; adds `scan_mode` + `igaming_brand_id` to `airdrop_transactions`; drops `network` from `airdrop_tokens`; seeds `active_network` + `igaming_threshold_usd` in `airdrop_config` |

### Workflow rules

- **Never edit an applied migration.** Add a new one.
- **Don't call `Base.metadata.create_all` anywhere.** It exists in SQLAlchemy but is not part of how this app manages schema.
- The ORM (`backend/db_models.py`) and the migrations must stay in sync. If you change a model, write the migration in the same change.
- For bulk inserts, mind the PostgreSQL **32 767 bind-params per statement** limit. The airdrop monitor caps batches at 2900 rows × 11 columns (see `backend/services/airdrop_monitor.py`). Apply the same logic for any new bulk-write code.
