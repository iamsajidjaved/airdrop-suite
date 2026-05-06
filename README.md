# Multi-Network Wallet Transaction Explorer

A web application for exploring cryptocurrency wallet transactions across Ethereum (ERC-20) and Tron (TRC-20) networks, with a built-in airdrop monitor that scans ERC-20 contracts for large stablecoin transfers and stores them in PostgreSQL.

## Features

- **Wallet exploration** — live lookup of ETH / ERC-20 transactions via Etherscan and TRX / TRC-20 via TronGrid.
- **Airdrop monitor** — scheduled or on-demand scan of configured ERC-20 tokens (e.g. USDT, USDC) for transfers ≥ a configurable USD threshold; deduplicated and persisted to Postgres.
- **Admin panel** — manage monitored tokens, edit the USD threshold, browse stored transfers, trigger monitor runs.
- **Filtering & export** — date range, network, counterparty, and direction filters; CSV export.
- **Dark UI** — vanilla JS + CSS, no build step.

## Tech stack

**Backend:** Python 3.11+, FastAPI, async SQLAlchemy + asyncpg, Alembic, `httpx`, `pydantic-settings`. Managed with [`uv`](https://github.com/astral-sh/uv).

**Frontend:** Vanilla HTML / CSS / JS — no framework, no bundler, no npm.

**Database:** PostgreSQL (Neon in production).

## Setup

### Prerequisites
- Python 3.11+
- `uv` ([install guide](https://github.com/astral-sh/uv))
- A PostgreSQL database (local or Neon)

### Install & run

```bash
uv sync                                          # install dependencies
uv run alembic upgrade head                      # apply DB migrations (first time + after pulls)
uv run uvicorn backend.main:app --reload \
    --host 127.0.0.1 --port 8000
```

Or use `start.ps1` / `start.bat` — they wrap `uv sync` + uvicorn. Run `alembic upgrade head` yourself the first time.

Open:
- `http://127.0.0.1:8000/` — wallet input
- `http://127.0.0.1:8000/explorer` — transaction dashboard
- `http://127.0.0.1:8000/admin/airdrop` — airdrop admin
- `http://127.0.0.1:8000/docs` — auto-generated OpenAPI docs

### Environment variables (`.env`)

Required:
- `ETHERSCAN_API_KEY` — Etherscan v2 API key
- `TRONGRID_API_KEY` — TronGrid API key
- `DATABASE_URL` — Postgres connection string. Any of `postgres://`, `postgresql://`, `postgresql+psycopg2://`, `postgresql+asyncpg://` works; `backend/db.py` translates between sync (Alembic) and async (runtime) forms and handles libpq's `sslmode` / `channel_binding` query params.

Optional:
- `HOST` (default `127.0.0.1`), `PORT` (default `8000`)
- `AIRDROP_PAGE_SIZE` (default `1000`) — Etherscan page size used by the airdrop monitor
- `AIRDROP_SEED_TOKENS`, `AIRDROP_SEED_THRESHOLD_USD` — only consumed by the initial seed migration; change them at runtime via the admin UI / API

## API endpoints

A short summary — see [`.claude/specs/api.md`](.claude/specs/api.md) for full request/response shapes.

**Health & wallet (stateless):**
- `GET /api/health`
- `POST /api/validate-wallet`
- `GET /api/transactions/{address}` — supports `from_date`, `to_date`, `networks` query params

**Airdrop monitor:**
- `POST /api/airdrop/monitor/run` — optional `?start_block_override=N` for backfills
- `GET /api/airdrop/status`
- `GET /api/airdrop/transactions` — paginated, filterable by token, address, amount, date

**Airdrop admin (token CRUD + config):**
- `GET / POST /api/airdrop/tokens`
- `PATCH / DELETE /api/airdrop/tokens/{id}`
- `GET / PUT /api/airdrop/config`

## Running the airdrop monitor from the CLI

```bash
uv run python scripts/monitor_airdrops.py
```

Same code path as `POST /api/airdrop/monitor/run`. Useful for cron / scheduler hooks.

## Project structure

```
wallet-explorer/
├── CLAUDE.md                  AI-tooling orientation (start here when contributing)
├── README.md                  this file
├── DEPLOYMENT.md              Render.com deployment guide
├── pyproject.toml             uv project + dependencies
├── alembic.ini                Alembic config
├── start.ps1 / start.bat      one-shot dev launchers
├── backend/
│   ├── CLAUDE.md              backend conventions
│   ├── main.py                FastAPI app + middleware + page routes
│   ├── config.py              pydantic-settings
│   ├── models.py              Pydantic request/response models
│   ├── db_models.py           SQLAlchemy ORM (airdrop tables)
│   ├── db.py                  async engine, session, URL translation
│   ├── routers/
│   │   ├── transactions.py    /api/validate-wallet, /api/transactions/{address}
│   │   └── airdrop.py         /api/airdrop/* (monitor, tokens, config, transactions)
│   ├── services/
│   │   ├── etherscan.py       async Etherscan v2 wrapper
│   │   ├── trongrid.py        async TronGrid wrapper
│   │   └── airdrop_monitor.py ERC-20 scanner
│   └── migrations/
│       └── versions/
│           ├── 0001_initial.py
│           └── 0002_seed_defaults.py
├── frontend/
│   ├── CLAUDE.md              frontend conventions
│   ├── index.html             wallet input
│   ├── explorer.html          transaction dashboard
│   ├── admin.html             airdrop admin
│   ├── css/                   styles.css, admin.css
│   └── js/                    wallet.js, explorer.js, admin.js
├── scripts/
│   └── monitor_airdrops.py    CLI runner for the airdrop monitor
└── .claude/
    ├── settings.local.json
    └── specs/
        ├── architecture.md
        ├── api.md
        ├── database.md
        └── airdrop-monitor.md
```

## Documentation

For contributors and AI tooling:

- [`CLAUDE.md`](CLAUDE.md) — top-level orientation (run, env vars, conventions)
- [`backend/CLAUDE.md`](backend/CLAUDE.md) — backend layout, request flow, how to add endpoints / migrations
- [`frontend/CLAUDE.md`](frontend/CLAUDE.md) — page/JS structure, no-build workflow
- [`.claude/specs/architecture.md`](.claude/specs/architecture.md) — system overview and data flow
- [`.claude/specs/api.md`](.claude/specs/api.md) — full endpoint reference
- [`.claude/specs/database.md`](.claude/specs/database.md) — schema, indexes, migration workflow
- [`.claude/specs/airdrop-monitor.md`](.claude/specs/airdrop-monitor.md) — scanner algorithm, idempotency, manual ops
- [`DEPLOYMENT.md`](DEPLOYMENT.md) — Render.com deployment + maintenance

## License

MIT
