# backend/CLAUDE.md

FastAPI backend. Async-first. Three feature surfaces: stateless wallet exploration, stateful airdrop scanning, and token distribution.

## Layout

```
backend/
├── main.py             FastAPI app: middleware, static mount, page routes, /api/health
├── config.py           pydantic-settings — env vars + defaults + NETWORKS registry
├── models.py           Pydantic request/response models (API boundary)
├── db_models.py        SQLAlchemy ORM models (scanner + distribution tables)
├── db.py               async engine, session factory, libpq → asyncpg URL translation
├── routers/
│   ├── transactions.py wallet validation + GET /api/transactions/{address}
│   ├── airdrop.py      scanner: monitor run, brands CRUD, tokens CRUD, config, txns
│   └── distribution.py distribution: campaigns, wallets, sends, worker control
├── services/
│   ├── etherscan.py    Etherscan v2 async wrapper (contract + address tokentx)
│   ├── trongrid.py     TronGrid async wrapper
│   ├── airdrop_monitor.py  dual-mode ERC-20 scanner (standard + iGaming)
│   ├── distribution_worker.py  background send loop (opt-in, off by default)
│   └── reset_service.py  wipe scan state (called by admin/reset endpoint)
└── migrations/
    ├── env.py          Alembic env (uses sync URL from backend.db.SYNC_DATABASE_URL)
    └── versions/       0001 → 0012 (see .claude/specs/database.md for inventory)
```

## Request flow

```
browser → FastAPI router → service → (Etherscan|TronGrid|Postgres) → Pydantic response
```

- `routers/transactions.py` calls `services/etherscan.py` and `services/trongrid.py` only — no DB.
- `routers/airdrop.py` uses `services/airdrop_monitor.py` (Etherscan + DB) and direct DB via `Depends(get_session)` for CRUD.
- `routers/distribution.py` uses `services/distribution_worker.py` + direct DB.

## Adding an endpoint

1. Add or extend a router under `backend/routers/`.
2. Define request/response models in `backend/models.py`.
3. Register the router in `backend/main.py` (`app.include_router(...)`).
4. If the endpoint touches the DB, take an `AsyncSession` via `Depends(get_session)`.

Existing routers prefix routes themselves (`APIRouter(prefix="/api", ...)`) — follow that pattern.

## Database

- ORM models: `backend/db_models.py` — covers scanner tables (`airdrop_tokens`, `airdrop_config`, `igaming_brands`, `airdrop_transactions`) and distribution tables.
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

Bind-parameter cap to remember: PostgreSQL allows max 32 767 bind params per statement. The airdrop monitor batches inserts in chunks of 2900 rows × 11 columns. Keep this in mind for any new bulk insert.

## Network configuration

`active_network` in `airdrop_config` is the single source of truth. The `NETWORKS` dict in `backend/config.py` maps network keys to Etherscan chain IDs and explorer URLs.

```python
from backend.config import get_active_network, chain_id_for, NETWORKS

# In an async endpoint/service:
network = await get_active_network(session)   # "ethereum" or "sepolia"
chain_id = chain_id_for(network)              # 1 or 11155111
```

There is no `ETHERSCAN_CHAIN_ID` env var and no per-token `network` field.

## Service patterns

Services in `backend/services/` follow the same shape:

- Module-level singleton (`etherscan_service`, `trongrid_service`, `monitor_service`) — import and use directly.
- httpx `AsyncClient` per call, with explicit timeout.
- Errors are caught + logged, not raised — callers get partial results rather than 500s.
- Returns are typed Pydantic models.

When adding a new external API service, mirror this structure and put it in `backend/services/`.

## Logging

```python
import logging
logger = logging.getLogger(__name__)
```

Root logger is configured in `backend/main.py:13-17` (level=INFO, standard format). No structured logging library; just stdlib.

## Configuration

`backend/config.py` defines a single `Settings` class loaded from `.env` + environment. Old env vars not in `Settings` are silently ignored (`extra="ignore"`). Import `from backend.config import settings` and read fields directly — don't pass settings around.

## Things not to do

- Don't add `Base.metadata.create_all(...)` anywhere. Use Alembic.
- Don't use `psycopg2` at runtime — it's only present for Alembic's sync engine.
- Don't introduce a synchronous request handler. Everything is `async def`.
- Don't bypass `backend/models.py` and return raw dicts from endpoints — use Pydantic response models so the OpenAPI docs at `/docs` stay accurate.
- Don't add a `network` field to tokens. Network is global; read it from `airdrop_config`.
