# API reference

All endpoints are served from the FastAPI app at `backend/main.py`. Auto-generated docs are available at `/docs` (Swagger UI) and `/redoc`. Pydantic models live in `backend/models.py`.

## Page routes (return HTML)

| Method | Path | Source |
| --- | --- | --- |
| GET | `/` | `frontend/index.html` |
| GET | `/explorer` | `frontend/explorer.html` |
| GET | `/admin/airdrop` | `frontend/admin.html` |

## Health

### `GET /api/health`
Returns `{ "status": "healthy", "service": "Wallet Explorer API", "version": "1.0.0" }`. No auth.

## Wallet (stateless)

### `POST /api/validate-wallet`
**Body:** `WalletValidationRequest` — `{ "address": "0x..." | "T..." }`
**Response:** `WalletValidationResponse` — `{ valid, network: "ethereum"|"tron"|null, message }`

Pure regex check. Source: `backend/routers/transactions.py:42-51`.

### `GET /api/transactions/{address}`
Fetch transactions for a wallet from Etherscan (Ethereum) or TronGrid (Tron). Network is inferred from address format.

**Path:** `address` — wallet to query.
**Query:**
- `from_date` *(optional, `YYYY-MM-DD`)* — inclusive lower bound (UTC).
- `to_date` *(optional, `YYYY-MM-DD`)* — inclusive upper bound (UTC, end-of-day).
- `networks` *(optional, comma-separated `ERC,TRC`)* — accepted but largely ignored; network is determined from the address itself.

**Response:** `TransactionResponse` — wallet address, total count, networks list, first/last activity timestamps, sorted-desc list of `Transaction`.

**Errors:** `400` if address format is invalid or dates are malformed.

Source: `backend/routers/transactions.py:54-156`.

## Airdrop — monitor

### `POST /api/airdrop/monitor/run`
Trigger one scanning pass across all active tokens.

**Query:**
- `start_block_override` *(optional, int)* — force every token to start from this block. When set, `last_scanned_block` is **not** updated (useful for backfills without losing the cursor).

**Response:** `MonitorRunResult` — `tokens_scanned`, `new_transfers_inserted`, `total_transfers_stored`, `blocks_scanned_per_token`, `run_timestamp`, `errors[]`.

Source: `backend/routers/airdrop.py:38-47`.

### `GET /api/airdrop/status`
**Response:** `AirdropStatusResponse` — `last_run_timestamp` (max `created_at` in `airdrop_transactions`), `last_block_per_token`, `total_transfers`.

Source: `backend/routers/airdrop.py:50-52`.

## Airdrop — tokens (CRUD)

### `GET /api/airdrop/tokens`
**Response:** `list[AirdropTokenOut]`, ordered by symbol.

### `POST /api/airdrop/tokens`
**Body:** `AirdropTokenCreate` — `symbol` (uppercased), `contract_address` (lowercased, validated `0x[0-9a-f]{40}`), `decimals`, `network` (default `ethereum`), `is_active` (default `true`).
**Response:** `AirdropTokenOut`. Returns `409` if `symbol` or `contract_address` already exists (uniqueness enforced at DB level).

### `PATCH /api/airdrop/tokens/{token_id}`
**Body:** `AirdropTokenUpdate` (any subset of fields, including `last_scanned_block`).
**Response:** `AirdropTokenOut`. `404` if not found, `409` on duplicate symbol/contract.

### `DELETE /api/airdrop/tokens/{token_id}`
**Response:** `204` on success. `404` if not found. **`409` if the token has any rows in `airdrop_transactions`** — set `is_active=false` instead. This is a safety guard, not a soft delete.

Source: `backend/routers/airdrop.py:129-193`.

## Airdrop — config

The only config key today is `min_threshold_usd`. Stored as a single row in `airdrop_config` keyed by `min_threshold_usd`.

### `GET /api/airdrop/config`
**Response:** `AirdropConfigOut` — `{ "min_threshold_usd": 500.0 }`. Returns the default `500.0` if no row exists.

### `PUT /api/airdrop/config`
**Body:** `AirdropConfigUpdate` — `{ "min_threshold_usd": float > 0 }`.
**Response:** `AirdropConfigOut`. Upserts the row.

Source: `backend/routers/airdrop.py:198-217`.

## Airdrop — transactions

### `GET /api/airdrop/transactions`
Paginated query over stored transfers.

**Query:**
- `token` — symbol filter (e.g. `USDT`); resolved to a `token_id` server-side.
- `from_address`, `to_address` — exact match (lowercased before comparison).
- `min_amount` — inclusive `amount >= ?`.
- `from_date`, `to_date` — `YYYY-MM-DD` (UTC). `to_date` is treated as end-of-day.
- `limit` — 1..500, default 50.
- `offset` — ≥ 0, default 0.

**Response:** `AirdropTransactionListResponse` — `{ total, limit, offset, items: AirdropTransactionOut[] }`. Items are sorted `transferred_at DESC, id DESC` and joined to `airdrop_tokens` so `token_symbol` is populated.

Source: `backend/routers/airdrop.py:57-124`.

## Error shape

FastAPI's default `{ "detail": "..." }` JSON is used throughout. There is a `models.ErrorResponse` defined but the routers raise plain `HTTPException`s, so consumers should treat `detail` as authoritative.
