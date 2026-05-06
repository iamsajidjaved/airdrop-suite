# CLAUDE.md — wallet-explorer

Orientation file for Claude Code and other AI tooling. Start here, then drill into per-area docs as needed.

## What this project is

A multi-network cryptocurrency wallet explorer with two distinct features:

1. **Wallet exploration** — read-only lookup of transactions for an Ethereum (ERC-20) or Tron (TRC-20) address, fetched live from Etherscan / TronGrid. Stateless.
2. **Airdrop monitor** — a stateful background scanner that walks ERC-20 token contracts (USDT, USDC, …) for transfers ≥ a configurable USD threshold, deduplicates them, and stores them in PostgreSQL. Has its own admin UI.

The two share the FastAPI app and DB connection pool but are otherwise independent.

## Tech stack

- **Backend:** Python 3.11+, FastAPI, async SQLAlchemy + asyncpg, Alembic migrations, `httpx` async client, `pydantic-settings`
- **Frontend:** Vanilla HTML / CSS / JS — no build step, no framework, no npm
- **Database:** PostgreSQL (Neon in production)
- **Package manager:** [`uv`](https://github.com/astral-sh/uv)

## Repo map

```
wallet-explorer/
├── CLAUDE.md                  this file
├── README.md                  user-facing setup + feature overview
├── DEPLOYMENT.md              Render.com deployment guide (still current)
├── pyproject.toml             uv project + deps
├── alembic.ini                Alembic config (script_location = backend/migrations)
├── start.ps1 / start.bat      one-shot dev launchers (uv sync + uvicorn --reload)
├── .env                       secrets (ETHERSCAN_API_KEY, TRONGRID_API_KEY, DATABASE_URL)
├── backend/                   FastAPI app — see backend/CLAUDE.md
├── frontend/                  static HTML/CSS/JS — see frontend/CLAUDE.md
├── scripts/
│   └── monitor_airdrops.py    CLI runner for the airdrop monitor (same code path as the API trigger)
└── .claude/
    ├── settings.local.json    local permission allowlist
    └── specs/                 deeper reference docs (see "Specs" below)
```

## Running locally

```bash
uv sync                          # install deps
uv run alembic upgrade head      # apply DB migrations (safe to run repeatedly)
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Or use `start.ps1` / `start.bat` — they wrap the `uv sync` + uvicorn step (they do NOT run migrations; do that yourself the first time).

App routes once running:
- `http://127.0.0.1:8000/` — wallet input (`frontend/index.html`)
- `http://127.0.0.1:8000/explorer` — transaction dashboard
- `http://127.0.0.1:8000/admin/airdrop` — airdrop admin
- `http://127.0.0.1:8000/docs` — FastAPI auto-docs

## Environment variables

Required (in `.env`):
- `ETHERSCAN_API_KEY` — Etherscan v2 API key
- `TRONGRID_API_KEY` — TronGrid API key
- `DATABASE_URL` — Postgres connection string. Either `postgres://`, `postgresql://`, `postgresql+psycopg2://`, or `postgresql+asyncpg://` works; `backend/db.py` translates between sync (Alembic) and async (runtime) forms and handles libpq `sslmode` / `channel_binding` params.

Optional:
- `HOST`, `PORT` — uvicorn bind (defaults `127.0.0.1`, `8000`)
- `AIRDROP_PAGE_SIZE` — Etherscan page size for the monitor (default `1000`)
- `AIRDROP_SEED_TOKENS`, `AIRDROP_SEED_THRESHOLD_USD` — only consumed by the initial seed migration

## Conventions

- **Async-first.** All runtime I/O (DB, external APIs) is async. Alembic is the only place sync DB code lives.
- **Pydantic for the API boundary.** Request and response shapes live in `backend/models.py`. SQLAlchemy ORM models are separate (`backend/db_models.py`) and only cover the airdrop tables.
- **Schema source of truth = Alembic migrations**, not `db_models.py`. Never edit an applied migration; add a new one.
- **No frontend build step.** Edit HTML/CSS/JS and refresh the browser. Static assets are served via `/static` with `no-store` cache headers (see `backend/main.py:41-48`), so changes always show up.
- **Logging:** `logger = logging.getLogger(__name__)` per module. Root config in `backend/main.py:13-17`.
- **No tests yet.** Verify changes by running the app and exercising the relevant page or endpoint.

## Where to look for what

| Question | Place to look |
| --- | --- |
| How is the backend organized? | `backend/CLAUDE.md` |
| How are pages / JS organized? | `frontend/CLAUDE.md` |
| Big picture / data flow | `.claude/specs/architecture.md` |
| Endpoint reference | `.claude/specs/api.md` |
| DB schema + migration workflow | `.claude/specs/database.md` |
| How the airdrop scanner works | `.claude/specs/airdrop-monitor.md` |
| Production deployment | `DEPLOYMENT.md` |

When in doubt, the code is small enough to read directly — `backend/` is ~10 files.
