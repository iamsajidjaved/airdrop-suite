# Multi-Network Wallet Transaction Explorer

A web application for exploring cryptocurrency wallet transactions across Ethereum (ERC-20) and Tron (TRC-20) networks, with a built-in airdrop monitor that scans ERC-20 contracts for large stablecoin transfers and stores them in PostgreSQL.

## Features

- **Wallet exploration** — live lookup of ETH / ERC-20 transactions via Etherscan and TRX / TRC-20 via TronGrid.
- **Airdrop monitor** — scheduled or on-demand scan of configured ERC-20 tokens (e.g. USDT, USDC) for transfers ≥ a configurable USD threshold; deduplicated and persisted to Postgres.
- **Quality filter** — multi-stage rejection of contracts, aggregators, dormant singletons and blocklisted addresses, so only real recipient candidates land in the pool.
- **Token distribution** — sign and broadcast ERC-20 transfers from sender wallets stored encrypted at rest. Per-campaign **sender mode** (single or multi-wallet parallel) and explicit **sender wallet assignment**; minimum-unit helper in the campaign UI.
- **Admin panel** — manage monitored tokens (including overriding `last_scanned_block` per token), edit the USD threshold, browse stored transfers, run the monitor, and manage campaigns / sender wallets.
- **Filtering & export** — date range, network, counterparty, and direction filters; CSV export.
- **Modern dashboard UI** — vanilla JS + CSS, no build step. Metronic-inspired layout with KPI tiles, gradient accents, and per-campaign progress.

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
- `ETHERSCAN_CHAIN_ID` (default `1`) — Etherscan v2 chain selector. `1` = mainnet, `11155111` = Sepolia, `17000` = Holesky. Must agree with `ETH_RPC_URL` and the contract addresses in `airdrop_tokens`.
- `NETWORK_ENVIRONMENT` (default `mainnet`) — informational label.

Distribution / quality (Phase 2 + 3):
- `ETH_RPC_URL` — JSON-RPC node used by the distribution worker, the on-chain `eth_getCode` quality probe, and (when set) wallet balance lookups. Use a Sepolia RPC for testnet runs.
- `AIRDROP_KEK` — base64 of 32 random bytes; encrypts sender private keys at rest. Generate: `uv run python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"`
- `AIRDROP_ADMIN_TOKEN` — shared secret required by `X-Admin-Token` on every write endpoint (token CRUD, blocklist, reset, distribution). Leave empty to disable auth in local dev.
- `DISTRIBUTION_WORKER_ENABLED` (default `false`) — flip to `true` to auto-start the sender worker on boot.

Quality filter tuning (sane defaults; only change if you know the trade-offs):
- `QUALITY_FILTER_ENABLED` (default `true`) — master switch
- `QUALITY_CONTRACT_CHECK_ENABLED` (default `true`) — inline `eth_getCode` lookups (requires `ETH_RPC_URL`)
- `QUALITY_PER_RUN_AGGREGATOR_DROP_THRESHOLD` (default `50`) — recipient seen this many times in one batch is treated as an exchange/router
- `QUALITY_MAX_INBOUND_COUNT` (default `1000`), `QUALITY_MAX_DISTINCT_SENDERS` (default `500`) — post-insert prune thresholds
- `QUALITY_DORMANT_SINGLETON_DAYS` (default `180`) — single-tx dormant wallets older than this are dropped

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
- `PATCH / DELETE /api/airdrop/tokens/{id}` — PATCH accepts `last_scanned_block` to override / reset block tracking per token (the admin UI exposes this via the **Set block** action).
- `GET / PUT /api/airdrop/config`
- `GET /api/airdrop/quality/stats`, `GET /api/airdrop/quality/blocklist`
- `POST / DELETE /api/airdrop/quality/blocklist` *(admin)*
- `POST /api/airdrop/quality/prune` *(admin)*
- `POST /api/airdrop/admin/reset` *(admin — see "Resetting the database" below)*

**Distribution (admin):**
- `GET / POST / PATCH / DELETE /api/distribution/wallets`
- `GET / POST /api/distribution/campaigns` — `POST` body accepts `sender_mode` (`"single"` | `"multi"`, default `"multi"`) and `sender_wallet_ids` (list of wallet ids; empty = use all active wallets).
- `PATCH /api/distribution/campaigns/{id}` — may also update `sender_mode`.
- `PUT /api/distribution/campaigns/{id}/wallets` — replace the assigned wallet set; body `{ "sender_wallet_ids": [1, 2] }`.
- `POST /api/distribution/campaigns/{id}/build|start|pause|retry-failed`
- `GET /api/distribution/campaigns/{id}/recipients`
- `GET / POST /api/distribution/worker/{start|stop}`

## Running the airdrop monitor from the CLI

```bash
uv run python scripts/monitor_airdrops.py
```

Same code path as `POST /api/airdrop/monitor/run`. Useful for cron / scheduler hooks.

## High-quality wallet collection

The airdrop monitor is paired with a multi-stage filter (see `.claude/specs/airdrop-monitor.md` and `quality_*` settings) so that only addresses likely to belong to real, engaged users land in `airdrop_transactions` — the eventual recipient pool for distribution campaigns.

What the filter rejects:
- **USD threshold** — `airdrop_config.min_threshold_usd` (default `500`); raise it from the admin UI for stricter selection.
- **Blocklist** — zero/dead/sentinel addresses by default; extend at runtime via `POST /api/airdrop/quality/blocklist`. Sender wallets in `distribution_wallets` are auto-excluded so the system never airdrops to itself.
- **Smart contracts** — `eth_getCode` lookup (cached in `wallet_contract_cache`) drops any recipient that is a contract.
- **Self-transfers** — `from == to` rows.
- **In-batch aggregators** — recipients appearing > `QUALITY_PER_RUN_AGGREGATOR_DROP_THRESHOLD` times in one Etherscan page (CEX hot wallets, routers).
- **Cumulative aggregators** — post-insert prune of recipients with > `QUALITY_MAX_INBOUND_COUNT` transfers or > `QUALITY_MAX_DISTINCT_SENDERS` distinct senders.
- **Dormant singletons** — wallets with one inbound transfer and no activity for `QUALITY_DORMANT_SINGLETON_DAYS`.

Manual re-run after tuning thresholds:
```bash
curl -X POST -H "X-Admin-Token: $AIRDROP_ADMIN_TOKEN" \
  "http://127.0.0.1:8000/api/airdrop/quality/prune?network=ethereum&enrich=true"
```

`GET /api/airdrop/quality/stats` reports blocklist size, cached contracts, total transactions, and distinct recipients — useful as a quick health check after a reset.

## Resetting the database

When switching networks (mainnet → Sepolia), changing token contracts, or simply starting over with stricter filters, wipe collected runtime data without touching the schema.

**CLI (recommended):**
```bash
uv run python scripts/reset_data.py            # interactive confirmation
uv run python scripts/reset_data.py --yes      # skip prompt
uv run python scripts/reset_data.py --include-blocklist --yes
uv run python scripts/reset_data.py --include-wallets --yes  # DESTRUCTIVE
```

**Admin API (same effect):**
```bash
curl -X POST -H "X-Admin-Token: $AIRDROP_ADMIN_TOKEN" \
  "http://127.0.0.1:8000/api/airdrop/admin/reset"

# include the blocklist (re-seeded on next `alembic upgrade head` re-run of 0004)
curl -X POST -H "X-Admin-Token: $AIRDROP_ADMIN_TOKEN" \
  "http://127.0.0.1:8000/api/airdrop/admin/reset?include_blocklist=true"

# include sender wallets — encrypted private keys are LOST and must be re-added
curl -X POST -H "X-Admin-Token: $AIRDROP_ADMIN_TOKEN" \
  "http://127.0.0.1:8000/api/airdrop/admin/reset?include_wallets=true"
```

Always truncated: `airdrop_transactions`, `wallet_contract_cache`, `distribution_campaigns`, `distribution_recipients`, `distribution_transactions`. Per-token `last_scanned_block` is reset to `NULL` so the next monitor pass starts from each token's configured start block.

Preserved: `airdrop_tokens`, `airdrop_config`, `quality_address_blocklist`, `distribution_wallets` (unless explicit flags are passed).

> Stop the scheduler (`POST /api/airdrop/scheduler/stop`) and the distribution worker (`POST /api/distribution/worker/stop`) before resetting in production to avoid races with in-flight inserts.

## Testnet (Sepolia) integration

The system is mainnet by default but designed to run end-to-end on Ethereum testnets. Use Sepolia for safe validation of the full pipeline (scan → filter → distribution → on-chain transfer) before pointing real funds at mainnet.

### 1. Provision testnet credentials

- **Etherscan v2 API key** — the same key works across all chains.
- **Sepolia JSON-RPC URL** — Alchemy, Infura, QuickNode, or any public Sepolia RPC.
- **Funded sender wallet** — get Sepolia ETH from a public faucet (e.g. `sepoliafaucet.com`); fund it with a Sepolia ERC-20 (mintable test USDC at `0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238`, or deploy your own).

### 2. Update `.env`

```env
ETHERSCAN_API_KEY=<your-key>
TRONGRID_API_KEY=<your-key>
DATABASE_URL=postgresql+asyncpg://...

# Network switch
ETHERSCAN_CHAIN_ID=11155111
NETWORK_ENVIRONMENT=sepolia
ETH_RPC_URL=https://eth-sepolia.g.alchemy.com/v2/<key>

# Replace mainnet token contracts with Sepolia ones (only consumed on first migration;
# after that, manage tokens via the admin UI). Sepolia USDC example below:
AIRDROP_SEED_TOKENS=USDC:0x1c7d4b196cb0c7b01d743fbc6116a902379c7238:6
AIRDROP_SEED_THRESHOLD_USD=10

# Distribution
AIRDROP_KEK=<base64-32-bytes>
AIRDROP_ADMIN_TOKEN=<random-shared-secret>
DISTRIBUTION_WORKER_ENABLED=false
```

> Threshold is in token units (USD-equivalent); on testnets where stablecoins have no real price, treat it as a raw-unit cutoff and lower it (e.g. `10`) so test transfers register.

### 3. Wipe mainnet data, then migrate

```bash
uv run python scripts/reset_data.py --include-wallets --yes
uv run alembic upgrade head
```

If migrations were already applied with mainnet seed values, edit the token list via the admin UI (`/admin/airdrop`) instead — `AIRDROP_SEED_TOKENS` is only read by migration `0002_seed_defaults` on a fresh DB.

### 4. Verify and run

```bash
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
# Trigger one scan
curl -X POST http://127.0.0.1:8000/api/airdrop/scheduler/trigger
# Inspect collected (filtered) recipients
curl http://127.0.0.1:8000/api/airdrop/transactions
```

Then build a dry-run distribution campaign from the admin UI (`/admin/distribution`), confirm the recipient list looks reasonable, flip `dry_run=false`, add a Sepolia-funded sender wallet, and start the worker. All on-chain transfers will hit Sepolia.

### 5. Promoting to mainnet

1. `uv run python scripts/reset_data.py --include-wallets --yes`
2. Set `ETHERSCAN_CHAIN_ID=1`, `NETWORK_ENVIRONMENT=mainnet`, point `ETH_RPC_URL` at a mainnet RPC.
3. Re-add mainnet token contracts (USDT/USDC) and your funded mainnet sender wallet via the admin UI.
4. Restart the app. Keep `DISTRIBUTION_WORKER_ENABLED=false` until a final dry-run campaign passes review.

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
