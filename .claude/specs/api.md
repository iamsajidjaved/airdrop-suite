# API reference

All endpoints are served from the FastAPI app at `backend/main.py`. Auto-generated docs are available at `/docs` (Swagger UI) and `/redoc`. Pydantic models live in `backend/models.py`.

## Page routes (return HTML)

| Method | Path | Source |
| --- | --- | --- |
| GET | `/` | `frontend/index.html` |
| GET | `/explorer` | `frontend/explorer.html` |
| GET | `/admin/scanner` | `frontend/admin.html` |
| GET | `/admin/airdrop` | `frontend/distribution.html` |
| GET | `/admin/settings` | `frontend/settings.html` |

## Health

### `GET /api/health`
Returns `{ "status": "healthy", "service": "Airdrop Suite API", "version": "1.0.0" }`. No auth.

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
Trigger one scanning pass.

**Query:**
- `scan_mode` *(default `"standard"`)* — one of `"standard"`, `"igaming"`, `"both"`. Controls which scanning logic runs.
- `start_block_override` *(optional, int)* — force every token/brand to start from this block. When set, cursors are **not** updated (useful for backfills without losing the cursor).

**Response:** `MonitorRunResult` — `tokens_scanned`, `brands_scanned`, `new_transfers_inserted`, `total_transfers_stored`, `blocks_scanned_per_token`, `blocks_scanned_per_brand`, `run_timestamp`, `errors[]`.

Source: `backend/routers/airdrop.py`.

### `GET /api/airdrop/status`
**Response:** `AirdropStatusResponse` — `last_run_timestamp`, `last_block_per_token`, `last_block_per_brand`, `total_transfers`, `scan_mode_breakdown` (`{ "standard": N, "igaming": N }`).

Source: `backend/routers/airdrop.py`.

### `GET /api/airdrop/networks`
**Response:** `list[dict]` — `[{ key, label, chain_id, explorer }, ...]`. Returns the static `NETWORKS` registry from `backend/config.py`.

## Airdrop — tokens (CRUD)

### `GET /api/airdrop/tokens`
**Response:** `list[AirdropTokenOut]`, ordered by symbol.

### `POST /api/airdrop/tokens`
**Body:** `AirdropTokenCreate` — `symbol` (uppercased), `contract_address` (lowercased, validated `0x[0-9a-f]{40}`), `decimals`, `is_active` (default `true`).
**Response:** `AirdropTokenOut`. Returns `409` if `symbol` or `contract_address` already exists.

Note: there is no `network` field on tokens. The active network is a global config key (`active_network` in `airdrop_config`).

### `PATCH /api/airdrop/tokens/{token_id}`
**Body:** `AirdropTokenUpdate` (any subset of fields, including `last_scanned_block`).
**Response:** `AirdropTokenOut`. `404` if not found, `409` on duplicate symbol/contract.

### `DELETE /api/airdrop/tokens/{token_id}`
**Response:** `204` on success. `404` if not found. **`409` if the token has any rows in `airdrop_transactions`** — set `is_active=false` instead. This is a safety guard, not a soft delete.

Source: `backend/routers/airdrop.py`.

## Airdrop — iGaming brands (CRUD)

### `GET /api/airdrop/brands`
**Response:** `list[IgamingBrandOut]`. Each brand includes a `transaction_count` field (computed from `airdrop_transactions`).

### `POST /api/airdrop/brands`
**Body:** `IgamingBrandCreate` — `name` (max 128), `wallet_address` (Ethereum address), `description` (optional, max 255), `is_active` (default `true`).
**Response:** `IgamingBrandOut` (201). Returns `409` if wallet_address already exists.

### `PATCH /api/airdrop/brands/{brand_id}`
**Body:** `IgamingBrandUpdate` (any subset of: `name`, `description`, `is_active`, `last_scanned_block`). Wallet address cannot be changed after creation.
**Response:** `IgamingBrandOut`. `404` if not found.

### `DELETE /api/airdrop/brands/{brand_id}`
**Response:** `204`. The FK on `airdrop_transactions.igaming_brand_id` is `ON DELETE SET NULL` — transactions are retained but unlinked.

Source: `backend/routers/airdrop.py`.

## Airdrop — config

Stored as key-value rows in `airdrop_config`. Keys used:

| Key | Default | Notes |
| --- | --- | --- |
| `min_threshold_usd` | `500.0` | USD floor for standard-mode transfers |
| `active_network` | `"ethereum"` | Blockchain for all scan operations (`"ethereum"` or `"sepolia"`) |
| `igaming_threshold_usd` | `0.0` | USD floor for iGaming-mode transfers (0 = capture all brand payouts) |

### `GET /api/airdrop/config`
**Response:** `AirdropConfigOut` — `{ min_threshold_usd, active_network, igaming_threshold_usd }`.

### `PUT /api/airdrop/config`
**Body:** `AirdropConfigUpdate` — any subset of `{ min_threshold_usd, active_network, igaming_threshold_usd }`. `active_network` must be a key in the `NETWORKS` registry.
**Response:** `AirdropConfigOut`. Upserts each supplied key.

Source: `backend/routers/airdrop.py`.

## Airdrop — transactions

### `GET /api/airdrop/transactions`
Paginated query over stored transfers.

**Query:**
- `token` — symbol filter (e.g. `USDT`); resolved to a `token_id` server-side.
- `scan_mode` — `"standard"` or `"igaming"` filter.
- `from_address`, `to_address` — exact match (lowercased before comparison).
- `min_amount` — inclusive `amount >= ?`.
- `from_date`, `to_date` — `YYYY-MM-DD` (UTC). `to_date` is treated as end-of-day.
- `limit` — 1..500, default 50.
- `offset` — ≥ 0, default 0.

**Response:** `AirdropTransactionListResponse` — `{ total, limit, offset, items: AirdropTransactionOut[] }`. Each item includes `scan_mode`, `brand_name` (nullable, populated for iGaming rows).

Source: `backend/routers/airdrop.py`.

## Airdrop — admin

### `POST /api/airdrop/admin/reset`
Reset scan state. Requires `X-Admin-Token` header.

**Body:** `{ "include_brands": bool }` — if true, also truncates `igaming_brands`; otherwise resets brand cursors only.

Clears `airdrop_transactions`, resets all `last_scanned_block` cursors, and optionally truncates brands.

## Error shape

FastAPI's default `{ "detail": "..." }` JSON is used throughout. There is a `models.ErrorResponse` defined but the routers raise plain `HTTPException`s, so consumers should treat `detail` as authoritative.
