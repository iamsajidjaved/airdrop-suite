# backend/CLAUDE.md

FastAPI backend. Async-first. Two feature surfaces: stateless wallet exploration and stateful airdrop monitoring.

## Layout

```
backend/
├── main.py             FastAPI app: middleware, static mount, page routes, /api/health
├── config.py           pydantic-settings — env vars + defaults
├── models.py           Pydantic request/response models (API boundary)
├── db_models.py        SQLAlchemy ORM models (airdrop tables only)
├── db.py               async engine, session factory, libpq → asyncpg URL translation
├── routers/
│   ├── transactions.py wallet validation + GET /api/transactions/{address}
│   └── airdrop.py      airdrop monitor + tokens + config + transactions CRUD
├── services/
│   ├── etherscan.py    Etherscan v2 async wrapper
│   ├── trongrid.py     TronGrid async wrapper
│   └── airdrop_monitor.py  ERC-20 contract scanner (DB-aware)
└── migrations/
    ├── env.py          Alembic env (uses sync URL from backend.db.SYNC_DATABASE_URL)
    └── versions/
        ├── 0001_initial.py        creates airdrop_* tables
        └── 0002_seed_defaults.py  seeds USDT/USDC + threshold from settings
```

## Request flow

```
browser → FastAPI router → service → (Etherscan|TronGrid|Postgres) → Pydantic response
```

- `routers/transactions.py` calls `services/etherscan.py` and `services/trongrid.py` only — no DB.
- `routers/airdrop.py` uses both `services/airdrop_monitor.py` (which talks to Etherscan + DB) and direct DB access via `Depends(get_session)` for CRUD.

## Adding an endpoint

1. Add or extend a router under `backend/routers/`.
2. Define request/response models in `backend/models.py`.
3. Register the router in `backend/main.py:37-38` (`app.include_router(...)`).
4. If the endpoint touches the DB, take an `AsyncSession` via `Depends(get_session)` (see `backend/db.py:78`).

Existing routers prefix routes themselves (`APIRouter(prefix="/api", ...)`, `APIRouter(prefix="/api/airdrop", ...)`) — follow that pattern.

## Database

- ORM models: `backend/db_models.py` (3 tables, all airdrop-related).
- Schema is owned by Alembic, not by `Base.metadata.create_all`. Never call `create_all`. Migrations are the source of truth.
- Async engine + session: `backend/db.py`. `get_session()` is the FastAPI dependency.
- Connection URL handling: `_to_asyncpg_url` strips libpq-only params (`sslmode`, `channel_binding`) and turns `sslmode=require` into `connect_args={"ssl": "require"}`. `_to_sync_url` is what Alembic uses.
- Pool: `pool_pre_ping=True`, `pool_recycle=300` — handles Neon's idle disconnects.

### Adding a migration

```bash
uv run alembic revision -m "add foo column"   # creates an empty file under backend/migrations/versions/
# fill in upgrade()/downgrade()
uv run alembic upgrade head
```

Bind-parameter cap to remember: PostgreSQL allows max 32 767 bind params per statement. The airdrop monitor batches inserts in chunks of 3000 rows × 10 columns for this reason (`backend/services/airdrop_monitor.py:208`). Keep this in mind for any new bulk insert.

## Service patterns

All three services in `backend/services/` follow the same shape:

- Module-level singleton (`etherscan_service`, `trongrid_service`, `monitor_service`) — import and use directly, or inject for testability (see `AirdropMonitorService.__init__`).
- httpx `AsyncClient` per call, with explicit timeout.
- Errors are caught + logged, not raised — callers get partial results rather than 500s.
- Returns are typed: Etherscan/TronGrid return `Transaction` Pydantic models; the airdrop monitor returns `MonitorRunResult`.

When adding a new external API service, mirror this structure and put it in `backend/services/`.

## Logging

```python
import logging
logger = logging.getLogger(__name__)
```

Root logger is configured in `backend/main.py:13-17` (level=INFO, standard format). No structured logging library; just stdlib.

## Configuration

`backend/config.py` defines a single `Settings` class loaded from `.env` + environment. Import `from backend.config import settings` and read fields directly — don't pass settings around.

## Things not to do

- Don't add `Base.metadata.create_all(...)` anywhere. Use Alembic.
- Don't use `psycopg2` at runtime — it's only present for Alembic's sync engine.
- Don't introduce a synchronous request handler. Everything is `async def`.
- Don't bypass `backend/models.py` and return raw dicts from endpoints — use Pydantic response models so the OpenAPI docs at `/docs` stay accurate.
