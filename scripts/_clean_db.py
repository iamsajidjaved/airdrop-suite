"""One-shot DB clean: truncate data tables, keep config/tokens/wallets."""
import asyncio
from sqlalchemy import text
from backend.db import engine

PRESERVE = {
    "alembic_version",
    "airdrop_config",
    "airdrop_tokens",
    "distribution_wallets",
}

TRUNCATE_ORDER = [
    "distribution_transactions",
    "distribution_recipients",
    "distribution_campaigns",
    "airdrop_transactions",
]


async def main() -> None:
    async with engine.begin() as conn:
        # Discover all public tables
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        )
        all_tables = {row[0] for row in result}
        print("All tables:", sorted(all_tables))

        # Tables to truncate = everything not preserved
        to_truncate = sorted(all_tables - PRESERVE)
        print("Will truncate:", to_truncate)

        if to_truncate:
            joined = ", ".join(to_truncate)
            await conn.execute(
                text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE")
            )

        # Reset last_scanned_block so monitor re-scans from scratch
        await conn.execute(text("UPDATE airdrop_tokens SET last_scanned_block = NULL"))

        # Report row counts for preserved tables
        for tbl in sorted(PRESERVE - {"alembic_version"}):
            if tbl in all_tables:
                r = await conn.execute(text(f"SELECT COUNT(*) FROM {tbl}"))
                print(f"  preserved {tbl}: {r.scalar()} rows")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
