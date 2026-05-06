# Token distribution (Airdrop Phase 2)

Stateful pipeline that distributes ERC-20 tokens from a pool of in-house **sender wallets** to recipient addresses harvested by the Phase 1 airdrop monitor. Source files:

- `backend/services/distribution_service.py` — CRUD + recipient build
- `backend/services/distribution_worker.py` — sender + receipt watcher loops
- `backend/services/web3_client.py` — AsyncWeb3 + ERC-20 helpers
- `backend/services/crypto.py` — AES-GCM private-key encryption
- `backend/routers/distribution.py` — `/api/distribution/*` endpoints
- `backend/auth.py` — `X-Admin-Token` dependency
- `frontend/distribution.html` + `frontend/js/distribution.js` — admin UI at `/admin/distribution`

## Purpose

Given recipient addresses already collected by the Phase 1 monitor (e.g. wallets that received ≥ $X of USDT), automatically send a fixed amount of an ERC-20 token to each one, in parallel across multiple sender wallets, with full audit trail and dry-run safety.

## Data flow

```
admin UI (/admin/distribution)
  │  X-Admin-Token
  ▼
/api/distribution/*  (FastAPI router)
  │
  ├─► distribution_service  ──► Postgres (4 tables)
  │       ├─ add/list/decrypt sender wallets
  │       ├─ create campaigns
  │       └─ build recipients   (SELECT DISTINCT to_address FROM airdrop_transactions
  │                              WHERE filters → INSERT … ON CONFLICT DO NOTHING)
  │
  └─► distribution_worker  (background, opt-in)
          ├─ _send_loop      claim recipient → sign+broadcast tx
          └─ _watch_loop     poll receipts → mark confirmed/failed
                  │
                  ▼
          web3_client (AsyncWeb3 → ETH_RPC_URL)
```

## Schema (migration `0003_distribution.py`)

| Table | Key columns |
| --- | --- |
| `distribution_wallets` | `address` unique, `encrypted_private_key BYTEA`, `key_nonce BYTEA(12)`, `is_active`, `label` |
| `distribution_campaigns` | `name`, `token_id → airdrop_tokens`, `amount_per_recipient NUMERIC(38,18)`, `network`, `status`, `dry_run`, `recipient_filter JSONB`, `max_total_amount` |
| `distribution_recipients` | `campaign_id`, `address`, `amount`, `status`, `assigned_wallet_id`, `attempts`, `last_error`, **UNIQUE(campaign_id, address)** |
| `distribution_transactions` | `recipient_id`, `wallet_id`, `nonce`, `tx_hash` unique nullable, EIP-1559 fees, `gas_used`, `block_number`, `status`, timestamps, `raw_error` |

Idempotent recipient build: `pg_insert(...).on_conflict_do_nothing(constraint="uq_dist_recipient_campaign_address")` in batches of 1000.

## State machines

**Campaign**: `draft → ready → running ⇄ paused → completed | failed`
- `draft` → `ready`: auto-transition when `build` inserts ≥ 1 recipient.
- `ready|paused` → `running`: `POST /campaigns/{id}/start`. Rejected if `dry_run=true`.
- `running` → `paused`: `POST /campaigns/{id}/pause`.
- `running` → `completed`: worker auto-marks when no `pending|sending|sent` recipients remain.

**Recipient**: `pending → sending → sent → confirmed | failed`
- `pending → sending`: worker claims the row with `SELECT … FOR UPDATE SKIP LOCKED`, increments `attempts`.
- `sending → sent`: tx broadcast OK, `tx_hash` recorded.
- `sent → confirmed`: receipt watcher saw a successful receipt.
- `* → failed`: terminal once `attempts ≥ distribution_max_retries_per_recipient`; otherwise reverts to `pending` for retry.
- `POST /campaigns/{id}/retry-failed` resets failed rows back to `pending` with `attempts=0`.

## Worker algorithm

Two loops run in `asyncio.gather`:

### `_send_loop` (every `distribution_worker_interval_seconds`, default 5s)

```
for each running, non-dry-run campaign:
    active_wallets = wallets where is_active=true
    semaphore = Semaphore(distribution_max_inflight)         # global throttle
    parallelize _process_one(campaign_id, wallet_id) across (campaign × wallet)

_process_one(campaign_id, wallet_id):
    async with per_wallet_lock[wallet_id]:                   # serialize per sender
        recipient = SELECT … WHERE status='pending'
                    ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
        if not recipient: return
        recipient.status = 'sending'; recipient.attempts += 1
        commit
        try:
            _send_to_recipient(...)
        except: _mark_recipient_failed(...)
```

### `_send_to_recipient`

1. Decrypt sender private key just-in-time (`crypto.decrypt_private_key`).
2. `nonce = eth.get_transaction_count(addr, "pending")` — pending nonce so back-to-back sends from the same wallet don't collide.
3. `(max_fee, tip) = estimate_fees()` — capped by `distribution_max_gas_price_gwei`.
4. `build_transfer_tx(...)` — estimates gas with 20 % margin, falls back to 100k.
5. `account.sign_transaction(tx)` → `eth.send_raw_transaction(signed.raw_transaction)`.
6. Insert `DistributionTransaction` row (`status='broadcast'`, `tx_hash` set).
7. Mark recipient `sent`.

### `_watch_loop` (every `distribution_receipt_poll_seconds`, default 12s)

```
for each tx with status='broadcast' (LIMIT 50):
    receipt = eth.get_transaction_receipt(tx_hash)
    if receipt is None: continue
    if receipt.status == 1:
        tx → 'success', recipient → 'confirmed'
    else:
        tx → 'reverted', recipient → 'failed' (terminal — does not retry on-chain reverts)
_maybe_complete_campaigns()
```

## Security

| Concern | Mitigation |
| --- | --- |
| Private keys at rest | AES-GCM (`cryptography.AESGCM`), 12-byte nonce, KEK from `AIRDROP_KEK` env (base64 of 32 bytes). Keys are decrypted only inside the worker, just before signing. |
| Admin endpoints | `X-Admin-Token` header checked against `AIRDROP_ADMIN_TOKEN`. Read endpoints are open; **all writes** (POST/PATCH/DELETE on wallets, campaigns, worker) require it. If the env var is empty, the dependency is a no-op (dev mode). |
| Accidental mainnet sends | Campaigns default to `dry_run=true`; `start` is rejected until you flip it via `PATCH /campaigns/{id}`. |
| Gas spikes | `distribution_max_gas_price_gwei` (default 100) caps `maxFeePerGas`. |
| Per-wallet abuse | `distribution_per_wallet_daily_cap` (default 0 = disabled) reserved for a future check; not yet enforced. |
| Concurrency | `with_for_update(skip_locked=True)` guarantees each recipient is claimed by one worker iteration; per-wallet `asyncio.Lock` serializes nonce use. |
| Wallet deletion | Blocked by 409 if any in-flight `broadcast` transactions reference the wallet. |

## Required env vars

```env
ETH_RPC_URL=https://sepolia.infura.io/v3/<key>          # or any mainnet/testnet JSON-RPC endpoint
AIRDROP_KEK=<base64 of 32 random bytes>                 # see "Generate KEK" below
AIRDROP_ADMIN_TOKEN=<long random string>                # required in production
DISTRIBUTION_WORKER_ENABLED=false                       # opt-in; flip to true to auto-start on boot
```

Tuning knobs (all optional, defaults shown):

```
DISTRIBUTION_WORKER_INTERVAL_SECONDS=5
DISTRIBUTION_RECEIPT_POLL_SECONDS=12
DISTRIBUTION_MAX_RETRIES_PER_RECIPIENT=3
DISTRIBUTION_MAX_GAS_PRICE_GWEI=100
DISTRIBUTION_MAX_INFLIGHT=8
DISTRIBUTION_PER_WALLET_DAILY_CAP=0
```

### Generate KEK

```powershell
uv run python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

Add the printed value to `.env` as `AIRDROP_KEK=...`. **Rotating the KEK invalidates every stored wallet** — re-add wallets after rotation.

## Endpoints

All under `/api/distribution`. Writes require `X-Admin-Token` (when `AIRDROP_ADMIN_TOKEN` is set).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/config` | Read-only worker + safety settings |
| GET | `/worker` | Worker state (`running`, last tick stats) |
| POST | `/worker/start` \| `/worker/stop` | Toggle worker at runtime |
| GET | `/wallets?include_balances=true` | List sender wallets (optional ETH + token balances) |
| POST | `/wallets` | Add wallet by private key (encrypted on insert) |
| PATCH | `/wallets/{id}` | Update label / `is_active` |
| DELETE | `/wallets/{id}` | Remove wallet (409 if in-flight) |
| GET | `/campaigns` | List campaigns (with token symbol + status counts) |
| GET | `/campaigns/{id}` | Campaign detail |
| POST | `/campaigns` | Create campaign (default `dry_run=true`) |
| PATCH | `/campaigns/{id}` | Update `name`, `dry_run`, `max_total_amount` |
| POST | `/campaigns/{id}/build` | Materialize recipients from `airdrop_transactions` per filter |
| POST | `/campaigns/{id}/start` | Move `ready|paused → running`. Rejects if `dry_run=true`. |
| POST | `/campaigns/{id}/pause` | `running → paused` |
| POST | `/campaigns/{id}/retry-failed` | Reset failed recipients to `pending` |
| GET | `/campaigns/{id}/recipients?status=&limit=&offset=` | Paginated recipients (with latest `tx_hash`) |

## Run-book

1. **One-time setup**
   - Generate `AIRDROP_KEK` and pick an `AIRDROP_ADMIN_TOKEN`; add both to `.env`.
   - Set `ETH_RPC_URL` to a Sepolia RPC URL for first-run validation.
   - `uv run alembic upgrade head` (already at revision `0003_distribution`).
2. **Add a sender wallet** at `/admin/distribution` (private key never leaves the server unencrypted; UI shows a warning banner).
3. **Fund** the wallet on Sepolia with the token + a little ETH for gas.
4. **Create campaign** with `dry_run=true` (default). Pick token, amount per recipient, and recipient filter (token symbol, date range, min USD, exclusions, limit).
5. **Build recipients** — idempotent; re-running with the same filter inserts nothing new.
6. Sanity-check the recipient list in the UI.
7. `PATCH /campaigns/{id}` with `dry_run=false`, then `POST /campaigns/{id}/start`.
8. Set `DISTRIBUTION_WORKER_ENABLED=true` (or `POST /api/distribution/worker/start` once) — worker drains `pending → sending → sent → confirmed`.
9. Watch the campaign progress bar; failed recipients stop retrying after `DISTRIBUTION_MAX_RETRIES_PER_RECIPIENT` attempts (default 3) and can be re-queued via `retry-failed`.

## Verified behavior (smoke test)

Against a freshly migrated DB with Phase 1 USDT data already present:

- `POST /campaigns` → 201 `{ id: 2, status: 'draft', dry_run: true }`
- `POST /campaigns/2/build` → `{ inserted: 25, total_recipients: 25 }`, status auto-promoted `draft → ready`.
- `GET /campaigns/2/recipients?limit=3` → 3 of 25 `pending` rows with `last_tx_hash: null`.
- `POST /campaigns/2/start` (still dry-run) → 400 `"Campaign is in dry_run mode; flip dry_run=false first."`
- `DELETE /wallets/2` → 204.
- Without `AIRDROP_KEK`: `POST /wallets` → 400 `"AIRDROP_KEK is not configured…"` (clean failure, no partial writes).

## Things this spec does **not** cover (yet)

- Non-EVM networks (Tron). The schema has a `network` column but `web3_client` is Ethereum-only.
- Price-aware caps (`distribution_per_wallet_daily_cap` is wired but not enforced).
- KEK rotation tooling — currently a manual re-add of wallets.
- Reorg handling — confirmations are recorded at first receipt; deep-finality re-checks are not implemented.
