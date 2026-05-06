"""
Reset collected runtime data so the airdrop monitor + distribution worker
start from a clean slate. Schema is preserved (Alembic owns DDL).

Usage:
    uv run python scripts/reset_data.py                  # safe default
    uv run python scripts/reset_data.py --include-blocklist
    uv run python scripts/reset_data.py --include-wallets
    uv run python scripts/reset_data.py --yes            # skip confirmation

Truncates: airdrop_transactions, wallet_contract_cache, distribution_campaigns,
distribution_recipients, distribution_transactions. Resets last_scanned_block
on every airdrop_tokens row.

--include-blocklist   also wipes quality_address_blocklist (seeded entries
                       are restored only by re-running migration 0004).
--include-wallets     also wipes distribution_wallets (encrypted private keys
                       cannot be recovered — re-add them via the admin UI).
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.services.reset_service import reset_collected_data


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Wipe collected wallet-explorer data")
    p.add_argument("--include-blocklist", action="store_true",
                   help="Also truncate quality_address_blocklist")
    p.add_argument("--include-wallets", action="store_true",
                   help="Also truncate distribution_wallets (DESTRUCTIVE)")
    p.add_argument("--yes", action="store_true",
                   help="Skip the interactive confirmation prompt")
    return p.parse_args()


async def main() -> int:
    args = parse_args()

    if not args.yes:
        scope = ["airdrop_transactions", "wallet_contract_cache",
                 "distribution_campaigns", "distribution_recipients",
                 "distribution_transactions"]
        if args.include_blocklist:
            scope.append("quality_address_blocklist")
        if args.include_wallets:
            scope.append("distribution_wallets (private keys lost)")
        print("About to TRUNCATE:")
        for t in scope:
            print(f"  - {t}")
        print("And reset last_scanned_block on all airdrop_tokens.")
        ans = input("Type 'reset' to confirm: ").strip().lower()
        if ans != "reset":
            print("Aborted.")
            return 2

    report = await reset_collected_data(
        include_blocklist=args.include_blocklist,
        include_wallets=args.include_wallets,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
