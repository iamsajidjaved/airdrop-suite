"""Etherscan API integration for Ethereum transactions"""
import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Optional
from backend.config import settings
from backend.models import Transaction

logger = logging.getLogger(__name__)

# Fallback chain_id when no explicit override is provided (Ethereum mainnet)
_DEFAULT_CHAIN_ID = 1


class EtherscanService:
    """Service for interacting with Etherscan v2 API."""

    def __init__(self):
        self.base_url = settings.etherscan_base_url
        self.api_key = settings.etherscan_api_key

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _tokentx(
        self,
        *,
        chain_id: int,
        start_block: int,
        end_block: int,
        page: int,
        offset: int,
        sort: str = "asc",
        contract_address: Optional[str] = None,
        address: Optional[str] = None,
    ) -> list[dict]:
        """Low-level wrapper for the Etherscan tokentx action.

        Provide ``contract_address`` for standard (contract-wide) scans, or
        ``address`` for address-based scans (iGaming mode).  Both can be
        combined to narrow to a specific token + wallet pair.
        """
        params: dict = {
            "chainid": str(chain_id),
            "module": "account",
            "action": "tokentx",
            "startblock": start_block,
            "endblock": end_block,
            "page": page,
            "offset": offset,
            "sort": sort,
            "apikey": self.api_key,
        }
        if contract_address:
            params["contractaddress"] = contract_address
        if address:
            params["address"] = address

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(self.base_url, params=params)
            data = response.json()

        if data.get("status") == "1" and data.get("result"):
            return data["result"]
        if data.get("status") == "0":
            logger.warning("Etherscan tokentx status=0: %s", data.get("message"))
            return []
        logger.error("Unexpected Etherscan tokentx response: %s", data)
        return []

    # ------------------------------------------------------------------
    # Public scan methods used by the airdrop monitor
    # ------------------------------------------------------------------

    async def get_contract_token_transfers(
        self,
        contract_address: str,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 1000,
        chain_id: Optional[int] = None,
    ) -> list[dict]:
        """Fetch all ERC-20 transfers for a token contract (standard scan mode).

        ``chain_id`` overrides the default so the monitor can use the global
        active_network chain id rather than a hardcoded setting.
        """
        try:
            return await self._tokentx(
                chain_id=chain_id if chain_id is not None else _DEFAULT_CHAIN_ID,
                start_block=start_block,
                end_block=end_block,
                page=page,
                offset=offset,
                contract_address=contract_address,
            )
        except Exception as e:
            logger.error("Error fetching contract token transfers for %s: %s", contract_address, e)
            raise

    async def get_address_token_transfers(
        self,
        wallet_address: str,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 1000,
        chain_id: Optional[int] = None,
    ) -> list[dict]:
        """Fetch all ERC-20 transfers involving a wallet address (iGaming scan mode).

        Returns both incoming and outgoing transfers.  The caller is responsible
        for filtering to outgoing-only (from_address == wallet_address) when
        building the iGaming recipient list.
        """
        try:
            return await self._tokentx(
                chain_id=chain_id if chain_id is not None else _DEFAULT_CHAIN_ID,
                start_block=start_block,
                end_block=end_block,
                page=page,
                offset=offset,
                address=wallet_address,
            )
        except Exception as e:
            logger.error("Error fetching address token transfers for %s: %s", wallet_address, e)
            raise

    # ------------------------------------------------------------------
    # Public methods used by the wallet-explorer (stateless lookups)
    # ------------------------------------------------------------------

    async def get_normal_transactions(
        self,
        address: str,
        chain_id: int = _DEFAULT_CHAIN_ID,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 10000,
    ) -> list[dict]:
        """Fetch normal ETH transactions for an address."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {
                    "chainid": str(chain_id),
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": start_block,
                    "endblock": end_block,
                    "page": page,
                    "offset": offset,
                    "sort": "desc",
                    "apikey": self.api_key,
                }
                response = await client.get(self.base_url, params=params)
                data = response.json()
                if data.get("status") == "1" and data.get("result"):
                    return data["result"]
                if data.get("status") == "0":
                    logger.warning("Etherscan txlist status=0: %s", data.get("message"))
                    return []
                return []
        except Exception as e:
            logger.error("Error fetching normal ETH transactions for %s: %s", address, e)
            raise

    async def get_erc20_transactions(
        self,
        address: str,
        chain_id: int = _DEFAULT_CHAIN_ID,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 10000,
    ) -> list[dict]:
        """Fetch ERC-20 token transfers for an address (wallet explorer)."""
        try:
            return await self._tokentx(
                chain_id=chain_id,
                start_block=start_block,
                end_block=end_block,
                page=page,
                offset=offset,
                address=address,
                sort="desc",
            )
        except Exception as e:
            logger.error("Error fetching ERC-20 transactions for %s: %s", address, e)
            raise

    async def get_all_transactions(self, address: str, chain_id: int = _DEFAULT_CHAIN_ID) -> list[Transaction]:
        """Fetch all transactions (normal + ERC-20) for an address (wallet explorer)."""
        logger.info("Fetching all Ethereum transactions for %s", address)

        try:
            normal_txs, erc20_txs = await asyncio.gather(
                self.get_normal_transactions(address, chain_id=chain_id),
                self.get_erc20_transactions(address, chain_id=chain_id),
                return_exceptions=True,
            )
            if isinstance(normal_txs, Exception):
                logger.error("Failed to fetch normal transactions: %s", normal_txs)
                normal_txs = []
            if isinstance(erc20_txs, Exception):
                logger.error("Failed to fetch ERC-20 transactions: %s", erc20_txs)
                erc20_txs = []
        except Exception as e:
            logger.error("Error in asyncio.gather for get_all_transactions: %s", e)
            normal_txs = []
            erc20_txs = []

        transactions = []

        for tx in normal_txs:
            try:
                amount_eth = int(tx.get("value", "0")) / 1e18
                gas_used = int(tx.get("gasUsed", "0"))
                gas_price = int(tx.get("gasPrice", "0"))
                gas_fee_eth = (gas_used * gas_price) / 1e18
                direction = "outgoing" if tx["from"].lower() == address.lower() else "incoming"
                transactions.append(Transaction(
                    hash=tx["hash"],
                    network="ERC",
                    timestamp=int(tx["timeStamp"]),
                    datetime=datetime.utcfromtimestamp(int(tx["timeStamp"])).strftime("%Y-%m-%d %H:%M:%S"),
                    **{"from": tx["from"], "to": tx["to"]},
                    amount=f"{amount_eth:.8f}",
                    token_symbol="ETH",
                    token_contract=None,
                    direction=direction,
                    status="Success" if tx.get("txreceipt_status") == "1" else "Failed",
                    block_number=int(tx["blockNumber"]),
                    gas_fee=f"{gas_fee_eth:.8f}",
                ))
            except Exception as e:
                logger.debug("Skipping malformed ETH tx: %s", e)
                continue

        for tx in erc20_txs:
            try:
                decimals = int(tx.get("tokenDecimal", "18"))
                amount = int(tx.get("value", "0")) / (10 ** decimals)
                direction = "outgoing" if tx["from"].lower() == address.lower() else "incoming"
                gas_used = int(tx.get("gasUsed", "0"))
                gas_price = int(tx.get("gasPrice", "0"))
                gas_fee_eth = (gas_used * gas_price) / 1e18
                transactions.append(Transaction(
                    hash=tx["hash"],
                    network="ERC",
                    timestamp=int(tx["timeStamp"]),
                    datetime=datetime.utcfromtimestamp(int(tx["timeStamp"])).strftime("%Y-%m-%d %H:%M:%S"),
                    **{"from": tx["from"], "to": tx["to"]},
                    amount=f"{amount:.8f}",
                    token_symbol=tx.get("tokenSymbol", "UNKNOWN"),
                    token_contract=tx.get("contractAddress"),
                    direction=direction,
                    status="Success",
                    block_number=int(tx["blockNumber"]),
                    gas_fee=f"{gas_fee_eth:.8f}",
                ))
            except Exception as e:
                logger.debug("Skipping malformed ERC-20 tx: %s", e)
                continue

        transactions.sort(key=lambda x: x.timestamp, reverse=True)
        logger.info("Total Ethereum transactions processed: %d", len(transactions))
        return transactions


# Global service instance
etherscan_service = EtherscanService()
