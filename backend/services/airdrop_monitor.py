"""Airdrop monitor service — collects ERC-20 recipient addresses for large transfers."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.models import AirdropRecipient, AirdropStatusResponse, MonitorRunResult
from backend.services.etherscan import etherscan_service, EtherscanService

logger = logging.getLogger(__name__)

MAX_PAGES_PER_RUN = 10


class AirdropMonitorService:
    def __init__(self, etherscan: Optional[EtherscanService] = None):
        self._etherscan = etherscan or etherscan_service
        self._data_dir = Path(settings.airdrop_data_dir)
        self._threshold = settings.airdrop_threshold_usd
        self._page_size = settings.airdrop_page_size
        self._tokens = self._parse_tokens()
        self._recipients_file = self._data_dir / "recipients.json"
        self._state_file = self._data_dir / "monitor_state.json"

    def _parse_tokens(self) -> list[dict]:
        tokens = []
        for entry in settings.airdrop_tokens.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split(":")
            if len(parts) != 3:
                raise ValueError(f"Invalid airdrop_tokens entry: '{entry}'. Expected SYMBOL:contract:decimals")
            symbol, contract, decimals_str = parts
            tokens.append({
                "symbol": symbol.strip(),
                "contract": contract.strip().lower(),
                "decimals": int(decimals_str.strip()),
            })
        return tokens

    def _ensure_data_dir(self):
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _load_recipients(self) -> dict[str, dict]:
        if not self._recipients_file.exists():
            return {}
        try:
            data = json.loads(self._recipients_file.read_text(encoding="utf-8"))
            return {r["address"]: r for r in data.get("recipients", [])}
        except Exception as e:
            logger.error(f"Failed to load recipients file: {e}")
            return {}

    def _save_recipients(self, recipients: dict[str, dict], timestamp: str):
        sorted_list = sorted(recipients.values(), key=lambda r: r["first_seen_block"], reverse=True)
        payload = {
            "metadata": {
                "last_updated": timestamp,
                "total_count": len(sorted_list),
            },
            "recipients": sorted_list,
        }
        tmp = self._recipients_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self._recipients_file)

    def _load_state(self) -> dict:
        if not self._state_file.exists():
            return {"last_updated": None, "last_block_per_token": {}}
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to load state file: {e}")
            return {"last_updated": None, "last_block_per_token": {}}

    def _save_state(self, state: dict):
        tmp = self._state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self._state_file)

    def _filter_and_extract(self, txs: list[dict], token: dict) -> list[dict]:
        results = []
        decimals = token["decimals"]
        for tx in txs:
            try:
                amount = int(tx.get("value", "0")) / (10 ** decimals)
                if amount < self._threshold:
                    continue
                ts = int(tx.get("timeStamp", 0))
                results.append({
                    "address": tx["to"].lower(),
                    "first_seen_tx": tx.get("hash", ""),
                    "first_seen_token": token["symbol"],
                    "first_seen_contract": token["contract"],
                    "first_seen_amount": amount,
                    "first_seen_block": int(tx.get("blockNumber", 0)),
                    "first_seen_datetime_utc": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                })
            except Exception as e:
                logger.warning(f"Skipping tx due to parse error: {e}")
        return results

    async def _fetch_all_pages(self, token: dict, start_block: int) -> tuple[list[dict], int]:
        """Fetch up to MAX_PAGES_PER_RUN pages for a token. Returns (txs, max_block_seen)."""
        all_txs: list[dict] = []
        max_block = start_block

        for page in range(1, MAX_PAGES_PER_RUN + 1):
            try:
                txs = await self._etherscan.get_contract_token_transfers(
                    contract_address=token["contract"],
                    start_block=start_block,
                    page=page,
                    offset=self._page_size,
                )
            except Exception as e:
                logger.error(f"Error fetching page {page} for {token['symbol']}: {e}")
                break

            if not txs:
                break

            all_txs.extend(txs)

            # Track the highest block number seen
            for tx in txs:
                try:
                    max_block = max(max_block, int(tx.get("blockNumber", 0)))
                except Exception:
                    pass

            if len(txs) < self._page_size:
                break  # Last page

        return all_txs, max_block

    async def run_monitor(self, start_block_override: Optional[int] = None) -> MonitorRunResult:
        self._ensure_data_dir()

        recipients = self._load_recipients()
        state = self._load_state()
        last_blocks = state.get("last_block_per_token", {})

        now_str = datetime.now(tz=timezone.utc).isoformat()
        errors: list[str] = []
        tokens_scanned: list[str] = []
        blocks_scanned_per_token: dict[str, dict] = {}
        new_count = 0

        # Fetch all tokens concurrently
        fetch_tasks = []
        for token in self._tokens:
            start = start_block_override if start_block_override is not None else last_blocks.get(token["symbol"], 0)
            fetch_tasks.append(self._fetch_all_pages(token, start))

        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # Serially merge results
        for token, result in zip(self._tokens, results):
            symbol = token["symbol"]
            tokens_scanned.append(symbol)

            if isinstance(result, Exception):
                msg = f"{symbol}: {type(result).__name__}: {result}"
                logger.error(msg)
                errors.append(msg)
                blocks_scanned_per_token[symbol] = {"from": 0, "to": 0, "fetched": 0}
                continue

            txs, max_block = result
            start_used = (
                start_block_override
                if start_block_override is not None
                else last_blocks.get(symbol, 0)
            )
            blocks_scanned_per_token[symbol] = {
                "from": start_used,
                "to": max_block,
                "fetched": len(txs),
            }

            extracted = self._filter_and_extract(txs, token)
            for record in extracted:
                addr = record["address"]
                if addr not in recipients:
                    recipients[addr] = record
                    new_count += 1

            # Only advance state when not in override (re-scan) mode
            if start_block_override is None and max_block > last_blocks.get(symbol, 0):
                last_blocks[symbol] = max_block + 1

        self._save_recipients(recipients, now_str)

        if start_block_override is None:
            state["last_updated"] = now_str
            state["last_block_per_token"] = last_blocks
            self._save_state(state)

        logger.info(
            f"Monitor run complete. New recipients: {new_count}, Total: {len(recipients)}"
        )

        return MonitorRunResult(
            tokens_scanned=tokens_scanned,
            new_recipients_found=new_count,
            total_recipients_stored=len(recipients),
            blocks_scanned_per_token=blocks_scanned_per_token,
            run_timestamp=now_str,
            errors=errors,
        )

    def get_status(self) -> AirdropStatusResponse:
        state = self._load_state()
        recipients = self._load_recipients()
        return AirdropStatusResponse(
            last_run_timestamp=state.get("last_updated"),
            last_block_per_token=state.get("last_block_per_token", {}),
            total_recipients=len(recipients),
            data_directory=str(self._data_dir.resolve()),
        )

    def get_recipients(
        self,
        token_filter: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AirdropRecipient]:
        recipients = self._load_recipients()
        items = list(recipients.values())
        if token_filter:
            items = [r for r in items if r.get("first_seen_token", "").upper() == token_filter.upper()]
        items.sort(key=lambda r: r["first_seen_block"], reverse=True)
        page = items[offset: offset + limit]
        return [AirdropRecipient(**r) for r in page]
