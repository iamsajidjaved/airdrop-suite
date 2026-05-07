# Wallet Explorer

A multi-network cryptocurrency wallet explorer with a built-in **Transaction Scanner** that watches ERC-20 token contracts (USDT, USDC, …) for transfers ≥ a configurable USD threshold and stores them in PostgreSQL.

## Features

- **Wallet lookup** — live, stateless lookup of ETH / ERC-20 transactions via Etherscan and TRX / TRC-20 via TronGrid.
- **Transaction Scanner** — manually triggered scan of configured ERC-20 tokens for transfers ≥ a configurable USD threshold (default **$500**). Deduplicated and persisted to Postgres.
- **Mainnet ↔ Sepolia switch** — pick the active network from the header dropdown. Each token is bound to a network; the scanner uses the matching Etherscan v2 chain id automatically.
- **Token CRUD from the UI** — add, edit, enable/disable, delete tokens. Symbol, contract, decimals, network all configurable. No hard-coded token list.
- **Quality filter** — drops contracts, aggregators, dormant singletons, and blocklisted addresses.
- **Token distribution** — sign and broadcast ERC-20 transfers from sender wallets stored encrypted at rest. Per-campaign sender mode (single / multi-wallet).
- **Modern admin dashboard** — vanilla JS + CSS, no build step. KPI tiles, gradient buttons, tight layout.

## Tech stack

- **Backend:** Python 3.11+, FastAPI, async SQLAlchemy + asyncpg, Alembic, `httpx`, `pydantic-settings`. Managed with [`uv`](https://github.com/astral-sh/uv).
- **Frontend:** Vanilla HTML / CSS / JS — no framework, no bundler, no npm.
- **Database:** PostgreSQL.

## Setup

### Prerequisites
- Python 3.11+
- `uv` ([install guide](https://github.com/astral-sh/uv))
- A PostgreSQL database (local or hosted — Neon works out of the box)

### Install & run

```bash
uv sync                                          # install dependencies
uv run alembic upgrade head                      # apply DB migrations
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Or use `start.ps1` / `start.bat` — they wrap `uv sync` + uvicorn. Run `alembic upgrade head` yourself the first time.

Open:
- `http://127.0.0.1:8000/` — wallet input
- `http://127.0.0.1:8000/explorer` — transaction dashboard
- `http://127.0.0.1:8000/admin/airdrop` — **Transaction Scanner** admin
- `http://127.0.0.1:8000/admin/distribution` — distribution / campaigns admin
- `http://127.0.0.1:8000/docs` — auto-generated OpenAPI docs

### Environment variables (`.env`)

Required:
- `ETHERSCAN_API_KEY` — Etherscan v2 API key (one key works for all chains)
- `TRONGRID_API_KEY` — TronGrid API key
- `DATABASE_URL` — Postgres connection string. Any of `postgres://`, `postgresql://`, `postgresql+psycopg2://`, `postgresql+asyncpg://` works.

Optional:
- `HOST` (default `127.0.0.1`), `PORT` (default `8000`)
- `AIRDROP_PAGE_SIZE` (default `1000`) — Etherscan page size
- `AIRDROP_SEED_TOKENS`, `AIRDROP_SEED_THRESHOLD_USD` — only consumed by the initial seed migration; after that, manage everything from the UI

Distribution & quality (only if you use the on-chain sender):
- `ETH_RPC_URL` — JSON-RPC node (required by the distribution worker and the on-chain `eth_getCode` quality probe)
- `AIRDROP_KEK` — base64 of 32 random bytes; encrypts sender private keys at rest. Generate: `uv run python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"`
- `AIRDROP_ADMIN_TOKEN` — shared secret required by `X-Admin-Token` on every write endpoint. Leave empty to disable auth in local dev.
- `DISTRIBUTION_WORKER_ENABLED` (default `false`) — opt-in distribution sender

> **No scheduler / no cron.** The transaction scanner runs **only** when you click **Run Scan** in the admin UI (or hit `POST /api/airdrop/monitor/run`).

## How the Transaction Scanner works

1. Open `/admin/airdrop`.
2. Pick the active network (Ethereum Mainnet / Sepolia Testnet) in the header.
3. Set the **Minimum Transfer Threshold** (default **500** USD).
4. Add the tokens you want to monitor (symbol, contract address, decimals, network, active flag). Defaults seeded on first migration are USDT and USDC on mainnet.
5. Click **Run Scan**. The scanner walks each active token on the chosen network, filters transfers ≥ threshold, applies the quality filter, and stores results in `airdrop_transactions`.
6. The Qualifying Transactions table refreshes automatically.

Every setting — threshold, tokens, networks — lives in the database and is editable from the UI. Nothing is hard-coded.

### Switching networks (Mainnet ↔ Sepolia)

The active network selector is purely a UI/scope filter:
- **Adds** a new token defaults to the active network in the modal.
- **Tokens table** shows only tokens for the active network.
- **Run Scan** scans only tokens whose `network` matches the active network.
- **Qualifying transactions** table is filtered by the active network.

To use Sepolia:
1. In the header, switch the dropdown to **Sepolia Testnet**.
2. Add Sepolia token contracts (e.g. test USDC at `0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238`).
3. Click **Run Scan**.

A funded Sepolia ETH wallet is only needed if you intend to broadcast distribution transfers; the scanner itself only reads.

## API endpoints (summary)

**Health & wallet (stateless):**
- `GET /api/health`
- `POST /api/validate-wallet`
- `GET /api/transactions/{address}`

**Transaction Scanner:**
- `POST /api/airdrop/monitor/run?network=ethereum` — manual scan (only when called)
- `GET /api/airdrop/status`
- `GET /api/airdrop/networks`
- `GET /api/airdrop/transactions?network=&token=&limit=&offset=`

**Token & threshold (admin):**
- `GET / POST /api/airdrop/tokens`
- `PATCH / DELETE /api/airdrop/tokens/{id}`
- `GET / PUT /api/airdrop/config`

**Quality filter:**
- `GET /api/airdrop/quality/stats`, `GET /api/airdrop/quality/blocklist`
- `POST / DELETE /api/airdrop/quality/blocklist` *(admin)*
- `POST /api/airdrop/quality/prune` *(admin)*
- `POST /api/airdrop/admin/reset` *(admin)*

**Distribution (admin):**
- `GET / POST / PATCH / DELETE /api/distribution/wallets`
- `GET / POST / PATCH /api/distribution/campaigns`
- `PUT /api/distribution/campaigns/{id}/wallets`
- `POST /api/distribution/campaigns/{id}/{build,start,pause,retry-failed}`
- `GET /api/distribution/campaigns/{id}/recipients`
- `GET / POST /api/distribution/worker/{start,stop}`

## CLI scan (optional)

```bash
uv run python scripts/monitor_airdrops.py
```

Same code path as `POST /api/airdrop/monitor/run`. Useful for one-off backfills.

## Resetting the database

```bash
uv run python scripts/reset_data.py            # interactive
uv run python scripts/reset_data.py --yes      # skip prompt
uv run python scripts/reset_data.py --include-blocklist --yes
uv run python scripts/reset_data.py --include-wallets --yes  # DESTRUCTIVE — drops sender wallets
```

Always truncated: `airdrop_transactions`, `wallet_contract_cache`, distribution campaigns/recipients/transactions. Per-token `last_scanned_block` reset to NULL. Schema is preserved.

## Project structure

```
wallet-explorer/
├── README.md                  this file
├── CLAUDE.md                  AI-tooling orientation
├── pyproject.toml             uv project + dependencies
├── alembic.ini                Alembic config
├── start.ps1 / start.bat      one-shot dev launchers
├── backend/
│   ├── main.py                FastAPI app + page routes
│   ├── config.py              pydantic-settings + NETWORKS registry
│   ├── models.py              Pydantic request/response models
│   ├── db_models.py           SQLAlchemy ORM
│   ├── db.py                  async engine, session, URL translation
│   ├── auth.py                X-Admin-Token guard
│   ├── routers/
│   │   ├── transactions.py    /api/validate-wallet, /api/transactions/{address}
│   │   ├── airdrop.py         /api/airdrop/* — scanner, tokens, config, quality
│   │   └── distribution.py    /api/distribution/*
│   ├── services/
│   │   ├── etherscan.py       async Etherscan v2 wrapper (per-call chain id)
│   │   ├── trongrid.py        async TronGrid wrapper
│   │   ├── airdrop_monitor.py manual scanner (no scheduler)
│   │   ├── wallet_quality.py  blocklist / contract / aggregator gates
│   │   ├── crypto.py          AES-GCM encryption for sender keys
│   │   ├── web3_client.py     AsyncWeb3 wrapper for distribution
│   │   ├── distribution_service.py
│   │   ├── distribution_worker.py
│   │   └── reset_service.py
│   └── migrations/versions/   Alembic migrations (0001 → 0005)
├── frontend/
│   ├── index.html             wallet input
│   ├── explorer.html          wallet transaction dashboard
│   ├── admin.html             Transaction Scanner admin
│   ├── distribution.html      distribution admin
│   ├── css/                   styles.css, admin.css
│   └── js/                    wallet.js, explorer.js, admin.js, distribution.js
└── scripts/
    ├── monitor_airdrops.py    CLI runner for the scanner
    └── reset_data.py          CLI reset helper
```

## License

MIT
