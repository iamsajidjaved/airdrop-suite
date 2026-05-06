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

All three tables are defined in `backend/db_models.py`. Schema is owned by Alembic — the ORM is never used to create tables.

### `airdrop_tokens`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `INTEGER PRIMARY KEY` autoincrement | |
| `symbol` | `VARCHAR(32)` `NOT NULL UNIQUE` | uppercased on input |
| `contract_address` | `VARCHAR(64)` `NOT NULL UNIQUE` | lowercased, validated `0x[0-9a-f]{40}` |
| `decimals` | `INTEGER NOT NULL` | |
| `network` | `VARCHAR(32) NOT NULL DEFAULT 'ethereum'` | |
| `is_active` | `BOOLEAN NOT NULL DEFAULT true` | scanner only picks up active tokens |
| `last_scanned_block` | `BIGINT` nullable | resume cursor; updated after each successful run unless `start_block_override` was passed |
| `created_at`, `updated_at` | `TIMESTAMPTZ NOT NULL` | server defaults `now()`; `updated_at` has `ON UPDATE` |

### `airdrop_config`

| Column | Type | Notes |
| --- | --- | --- |
| `key` | `VARCHAR(64) PRIMARY KEY` | only `min_threshold_usd` is used today |
| `value` | `VARCHAR(255) NOT NULL` | stored as string; cast at read time |
| `updated_at` | `TIMESTAMPTZ NOT NULL` | |

### `airdrop_transactions`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `BIGINT PRIMARY KEY` autoincrement | |
| `tx_hash` | `VARCHAR(80) NOT NULL` | |
| `log_index` | `INTEGER NOT NULL DEFAULT 0` | needed because a single tx can contain many transfers |
| `block_number` | `BIGINT NOT NULL` | |
| `network` | `VARCHAR(32) NOT NULL DEFAULT 'ethereum'` | |
| `token_id` | `INTEGER NOT NULL FK → airdrop_tokens.id ON DELETE CASCADE` | |
| `from_address` | `VARCHAR(64) NOT NULL` | lowercased before insert |
| `to_address` | `VARCHAR(64) NOT NULL` | lowercased before insert |
| `amount` | `NUMERIC(38, 18) NOT NULL` | full token precision |
| `amount_usd` | `NUMERIC(38, 8)` nullable | for stablecoins, 1:1 with `amount` |
| `transferred_at` | `TIMESTAMPTZ NOT NULL` | from `timeStamp` of the transfer |
| `created_at` | `TIMESTAMPTZ NOT NULL` | when the row was written; `get_status` uses `MAX(created_at)` as "last run" |

**Constraints / indexes:**
- `UNIQUE (tx_hash, log_index, token_id)` as `uq_airdrop_tx_hash_log_token` — enables `ON CONFLICT DO NOTHING` for idempotent monitor reruns.
- `INDEX (token_id, block_number)` as `ix_airdrop_tx_token_block`.
- `INDEX (to_address)`, `INDEX (from_address)`, `INDEX (transferred_at)` for the admin transactions list filters.

## Migrations

Lives under `backend/migrations/`. Configured by `alembic.ini` (script_location = `backend/migrations`).

```bash
uv run alembic upgrade head                  # apply all pending
uv run alembic revision -m "add foo column"  # new empty migration
uv run alembic downgrade -1                  # roll back one (use with care)
uv run alembic current                       # show current revision
uv run alembic history                       # show migration graph
```

### Existing migrations

- `0001_initial.py` — creates `airdrop_tokens`, `airdrop_config`, `airdrop_transactions` with all indexes and constraints listed above.
- `0002_seed_defaults.py` — seeds USDT and USDC rows plus `min_threshold_usd=500.0` from `settings.airdrop_seed_tokens` and `settings.airdrop_seed_threshold_usd`. Read once at migration time; changing those env vars later does **not** reseed — use the admin UI / API.

### Workflow rules

- **Never edit an applied migration.** Add a new one.
- **Don't call `Base.metadata.create_all` anywhere.** It exists in SQLAlchemy but is not part of how this app manages schema.
- The ORM (`backend/db_models.py`) and the migrations must stay in sync. If you change a model, write the migration in the same change.
- For bulk inserts, mind the PostgreSQL **32 767 bind-params per statement** limit. The airdrop monitor caps batches at 3000 rows × 10 columns (see `backend/services/airdrop_monitor.py:208`). Apply the same logic for any new bulk-write code.
