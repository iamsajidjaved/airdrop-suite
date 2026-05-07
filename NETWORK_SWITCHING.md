# Switching networks (mainnet ↔ testnet)

This project can run end-to-end on Ethereum mainnet or on a public testnet
(Sepolia recommended). The switch is environment-driven; nothing in the code
needs to change. This guide documents the exact steps for both directions.

> **Why a guide and not a flag?** Three things must agree on a single chain
> at all times:
>
> 1. `ETHERSCAN_CHAIN_ID` — what Etherscan v2 reads from
> 2. `ETH_RPC_URL` — what the distribution worker writes to and what the
>    contract-quality probe queries
> 3. The `contract_address` of every row in `airdrop_tokens` — token
>    contracts on mainnet do **not** exist on Sepolia and vice-versa
>
> Mixing them silently produces empty scans, failed transactions, or — worst
> case — a mainnet send that you intended for a testnet. Always run the full
> reset between switches.

---

## Current state

The project is currently configured for **Sepolia testnet**. The previous
mainnet `.env` is preserved at `.env.mainnet.bak` for reference.

| Setting               | Value                                         |
| --------------------- | --------------------------------------------- |
| `ETHERSCAN_CHAIN_ID`  | `11155111` (Sepolia)                          |
| `NETWORK_ENVIRONMENT` | `sepolia`                                     |
| `ETH_RPC_URL`         | `https://rpc2.sepolia.org`                    |
| Monitored token       | Sepolia USDC `0x1c7d…7238` (6 decimals)       |
| `min_threshold_usd`   | `10` (raw token units on testnet)             |
| Sender wallets        | preserved from prior config                   |

---

## Pre-flight (always do these first)

1. **Stop the schedulers** (otherwise an in-flight scan or send can race the
   reset and produce orphan rows):

   ```powershell
   curl -X POST -H "X-Admin-Token: $env:AIRDROP_ADMIN_TOKEN" `
     http://127.0.0.1:8000/api/airdrop/scheduler/stop
   curl -X POST -H "X-Admin-Token: $env:AIRDROP_ADMIN_TOKEN" `
     http://127.0.0.1:8000/api/distribution/worker/stop
   ```

2. **Stop the dev server** (`Ctrl+C` in the terminal running `uvicorn`).

3. **Back up `.env`** so you can roll back without re-deriving secrets:

   ```powershell
   Copy-Item .env .env.bak -Force
   ```

---

## Mainnet → Testnet (Sepolia)

### 1. Provision testnet credentials

- A Sepolia JSON-RPC URL (Alchemy, Infura, or a public node like
  `https://rpc2.sepolia.org`).
- A Sepolia-funded sender wallet — get test ETH from a faucet
  (`sepoliafaucet.com`) and a Sepolia ERC-20 (e.g. mintable test USDC at
  `0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238`, or deploy your own).
- The same Etherscan v2 API key works on every chain — no change needed.

### 2. Update `.env`

```env
ETHERSCAN_CHAIN_ID=11155111
NETWORK_ENVIRONMENT=sepolia
ETH_RPC_URL=https://eth-sepolia.g.alchemy.com/v2/<key>

# Threshold is in token units; on testnets there is no real USD price,
# so treat it as a raw cutoff and lower it so test transfers register.
AIRDROP_SEED_TOKENS=USDC:0x1c7d4b196cb0c7b01d743fbc6116a902379c7238:6
AIRDROP_SEED_THRESHOLD_USD=10
```

`AIRDROP_SEED_*` are only consumed by Alembic migration `0002` on a fresh
database. After that, the live values come from the `airdrop_tokens` and
`airdrop_config` tables, which we update in the next step.

### 3. Wipe collected mainnet data

Sender wallets are preserved by default. Add `--include-wallets` only if you
want to abandon the mainnet sender keys (irrecoverable).

```powershell
uv run python scripts/reset_data.py --yes
```

This truncates `airdrop_transactions`, `wallet_contract_cache`,
`distribution_campaigns`, `distribution_recipients`,
`distribution_transactions`, and resets every token's `last_scanned_block`
to `NULL`.

### 4. Replace tokens with testnet contracts

Mainnet USDT/USDC contract addresses are invalid on Sepolia — leaving them
in `airdrop_tokens` would produce an empty scan. Either edit them in the
admin UI (`/admin/airdrop`) or via the API:

```powershell
$h = @{ 'Content-Type'='application/json'; 'X-Admin-Token'=$env:AIRDROP_ADMIN_TOKEN }

# Delete each mainnet token:
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/airdrop/tokens/1 -Method Delete -Headers $h
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/airdrop/tokens/2 -Method Delete -Headers $h

# Add Sepolia USDC:
$body = @{ symbol='USDC'; contract_address='0x1c7d4b196cb0c7b01d743fbc6116a902379c7238'; decimals=6; network='ethereum' } | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/airdrop/tokens -Method Post -Headers $h -Body $body

# Lower the threshold so test transfers register:
Invoke-RestMethod -Uri http://127.0.0.1:8000/api/airdrop/config -Method Put -Headers $h `
  -Body (@{ min_threshold_usd = 10 } | ConvertTo-Json)
```

(The server must be running for the API calls; alternatively use the admin
UI before stopping it in step 2.)

### 5. Verify and run

```powershell
uv run uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

# Trigger one scan
curl -X POST -H "X-Admin-Token: $env:AIRDROP_ADMIN_TOKEN" `
  http://127.0.0.1:8000/api/airdrop/scheduler/trigger

# Inspect collected (filtered) recipients
Invoke-RestMethod http://127.0.0.1:8000/api/airdrop/transactions
```

Then build a campaign from `/admin/distribution`. Confirm the recipient
list looks reasonable, attach a Sepolia-funded sender wallet, flip the
worker on, and watch transfers hit Sepolia.

---

## Testnet → Mainnet

Reverse direction. Treat with extra care: the next start-up will spend real
funds.

### 1. Final dry run on testnet

Before flipping anything, run one last campaign on testnet end-to-end with
`dry_run=true` and review the recipient list. If anything looks off,
fix it now — once mainnet is live, sends are irreversible.

### 2. Wipe testnet data

You almost always want a clean slate so testnet recipients are not carried
into a mainnet campaign. Decide whether to keep the testnet sender wallets:

```powershell
# Keep wallets (default; safe):
uv run python scripts/reset_data.py --yes

# Or wipe them too if the testnet sender keys won't be reused on mainnet:
uv run python scripts/reset_data.py --include-wallets --yes
```

### 3. Update `.env`

```env
ETHERSCAN_CHAIN_ID=1
NETWORK_ENVIRONMENT=mainnet
ETH_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/<key>

# Bootstrap-only; ignored once airdrop_tokens has rows. Restoring the
# mainnet defaults here is purely for documentation:
AIRDROP_SEED_TOKENS=USDT:0xdac17f958d2ee523a2206206994597c13d831ec7:6,USDC:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48:6
AIRDROP_SEED_THRESHOLD_USD=500

# Keep the worker OFF on first boot until you've confirmed the wallet pool:
DISTRIBUTION_WORKER_ENABLED=false
```

Leave `AIRDROP_KEK` and `AIRDROP_ADMIN_TOKEN` unchanged unless you're
deliberately rotating them. Rotating `AIRDROP_KEK` invalidates every
encrypted private key in `distribution_wallets`.

### 4. Replace tokens with mainnet contracts

Same procedure as testnet step 4, but with mainnet addresses:

| Symbol | Address                                      | Decimals |
| ------ | -------------------------------------------- | -------: |
| USDT   | `0xdac17f958d2ee523a2206206994597c13d831ec7` |        6 |
| USDC   | `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` |        6 |

Set `min_threshold_usd` back to `500` (or whatever your mainnet quality
target is).

### 5. Re-add mainnet sender wallet(s)

If you wiped wallets in step 2, add a funded mainnet sender via the admin
UI. Verify the on-screen address matches what you expect **before** funding
it — a typo at this stage is unrecoverable.

### 6. Boot, verify, then enable the worker

```powershell
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000

# Smoke test: one scheduler tick, inspect rows, confirm token contracts
curl -X POST -H "X-Admin-Token: $env:AIRDROP_ADMIN_TOKEN" `
  http://127.0.0.1:8000/api/airdrop/scheduler/trigger
Invoke-RestMethod http://127.0.0.1:8000/api/airdrop/transactions | Select-Object -First 5
```

Build a campaign with `dry_run=true`, review, then flip `dry_run=false`,
start the worker (`POST /api/distribution/worker/start`), and monitor the
first batch of sends on Etherscan before walking away.

---

## Common mistakes & how to detect them

| Symptom                                                  | Probable cause                                                     |
| -------------------------------------------------------- | ------------------------------------------------------------------ |
| Scanner runs forever, finds nothing                       | `ETHERSCAN_CHAIN_ID` and the token's contract are on different chains |
| `eth_getCode` errors / contract check disabled            | `ETH_RPC_URL` empty or pointed at the wrong chain                   |
| Distribution send reverts immediately                     | Sender wallet has no balance of the token on the active chain       |
| Send goes through but on the wrong chain                  | `ETH_RPC_URL` and the token contract you queued differ               |
| `last_scanned_block` is huge after a switch               | You forgot `reset_data.py`; the resume point is from the old chain   |

Quick health check after any switch:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/airdrop/status
Invoke-RestMethod http://127.0.0.1:8000/api/airdrop/quality/stats
```

`status` reports `network_environment` and the `last_scanned_block` per
token; if either contradicts your `.env`, stop and re-check.

---

## Recovery: rolling back to the previous `.env`

```powershell
# After a botched switch, restore the prior environment:
Copy-Item .env.bak .env -Force
# Then re-run the reset to clear any data collected under the wrong config:
uv run python scripts/reset_data.py --yes
```

If you had also rotated `AIRDROP_KEK` between switches, the encrypted
private keys in `distribution_wallets` cannot be decrypted with the old
KEK — you must re-import the sender wallets via the admin UI.
