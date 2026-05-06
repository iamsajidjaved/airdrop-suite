"""
Standalone airdrop monitor script.

Usage:
    uv run python scripts/monitor_airdrops.py
    uv run python scripts/monitor_airdrops.py --start-block 21500000
    uv run python scripts/monitor_airdrops.py --start-block 21500000 --pretty

Exit code 0 on success, 1 if any errors occurred (suitable for cron/Task Scheduler alerting).
"""
import asyncio
import argparse
import json
import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.services.airdrop_monitor import AirdropMonitorService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an airdrop monitor scan")
    parser.add_argument(
        "--start-block",
        type=int,
        default=None,
        help="Override start block for this run (does not update stored state)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    service = AirdropMonitorService()
    result = await service.run_monitor(start_block_override=args.start_block)
    print(json.dumps(result.model_dump(), indent=2 if args.pretty else None))
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
