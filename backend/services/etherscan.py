"""Etherscan API integration for Ethereum transactions"""
import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Optional
from backend.config import settings
from backend.models import Transaction

# Configure logging
logger = logging.getLogger(__name__)


class EtherscanService:
    """Service for interacting with Etherscan API"""
    
    def __init__(self):
        self.base_url = settings.etherscan_base_url
        self.api_key = settings.etherscan_api_key
    
    async def get_normal_transactions(
        self, 
        address: str, 
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 10000
    ) -> list[dict]:
        """Fetch normal ETH transactions"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {
                    "chainid": "1",  # Ethereum mainnet
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": start_block,
                    "endblock": end_block,
                    "page": page,
                    "offset": offset,
                    "sort": "desc",
                    "apikey": self.api_key
                }
                
                logger.info(f"Fetching ETH normal transactions for {address}")
                logger.debug(f"Etherscan URL: {self.base_url}")
                logger.debug(f"Params: {params}")
                
                response = await client.get(self.base_url, params=params)
                logger.info(f"Etherscan normal TX response status: {response.status_code}")
                
                data = response.json()
                logger.debug(f"Etherscan normal TX response: {data}")
                
                if data.get("status") == "1" and data.get("result"):
                    logger.info(f"Found {len(data['result'])} normal transactions")
                    return data["result"]
                elif data.get("status") == "0":
                    logger.warning(f"Etherscan API returned status 0: {data.get('message')}")
                    return []
                else:
                    logger.error(f"Unexpected Etherscan response: {data}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching normal ETH transactions: {type(e).__name__}: {e}")
            raise
    
    async def get_erc20_transactions(
        self, 
        address: str,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 10000
    ) -> list[dict]:
        """Fetch ERC-20 token transactions"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {
                    "chainid": "1",  # Ethereum mainnet
                    "module": "account",
                    "action": "tokentx",
                    "address": address,
                    "startblock": start_block,
                    "endblock": end_block,
                    "page": page,
                    "offset": offset,
                    "sort": "desc",
                    "apikey": self.api_key
                }
                
                logger.info(f"Fetching ERC-20 token transactions for {address}")
                logger.debug(f"Etherscan URL: {self.base_url}")
                logger.debug(f"Params: {params}")
                
                response = await client.get(self.base_url, params=params)
                logger.info(f"Etherscan ERC-20 response status: {response.status_code}")
                
                data = response.json()
                logger.debug(f"Etherscan ERC-20 response: {data}")
                
                if data.get("status") == "1" and data.get("result"):
                    logger.info(f"Found {len(data['result'])} ERC-20 transactions")
                    return data["result"]
                elif data.get("status") == "0":
                    logger.warning(f"Etherscan API returned status 0: {data.get('message')}")
                    return []
                else:
                    logger.error(f"Unexpected Etherscan response: {data}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching ERC-20 transactions: {type(e).__name__}: {e}")
            raise
    
    async def get_contract_token_transfers(
        self,
        contract_address: str,
        start_block: int = 0,
        end_block: int = 99999999,
        page: int = 1,
        offset: int = 1000
    ) -> list[dict]:
        """Fetch all ERC-20 transfers for a token contract (no wallet filter)."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                params = {
                    "chainid": "1",
                    "module": "account",
                    "action": "tokentx",
                    "contractaddress": contract_address,
                    "startblock": start_block,
                    "endblock": end_block,
                    "page": page,
                    "offset": offset,
                    "sort": "asc",
                    "apikey": self.api_key
                }

                logger.info(f"Fetching contract token transfers for {contract_address} from block {start_block}")

                response = await client.get(self.base_url, params=params)
                logger.info(f"Etherscan contract transfer response status: {response.status_code}")

                data = response.json()

                if data.get("status") == "1" and data.get("result"):
                    logger.info(f"Found {len(data['result'])} transfers for contract {contract_address}")
                    return data["result"]
                elif data.get("status") == "0":
                    logger.warning(f"Etherscan API returned status 0: {data.get('message')}")
                    return []
                else:
                    logger.error(f"Unexpected Etherscan response: {data}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching contract token transfers: {type(e).__name__}: {e}")
            raise

    async def get_all_transactions(self, address: str) -> list[Transaction]:
        """Fetch all transactions (normal + ERC-20) for an address"""
        logger.info(f"Starting to fetch all transactions for Ethereum address: {address}")
        
        try:
            # Fetch both types of transactions in parallel
            normal_txs, erc20_txs = await asyncio.gather(
                self.get_normal_transactions(address),
                self.get_erc20_transactions(address),
                return_exceptions=True
            )
            
            # Handle exceptions from gather
            if isinstance(normal_txs, Exception):
                logger.error(f"Failed to fetch normal transactions: {normal_txs}")
                normal_txs = []
            if isinstance(erc20_txs, Exception):
                logger.error(f"Failed to fetch ERC-20 transactions: {erc20_txs}")
                erc20_txs = []
                
        except Exception as e:
            logger.error(f"Error in asyncio.gather: {e}")
            normal_txs = []
            erc20_txs = []
        
        transactions = []
        
        # Process normal ETH transactions
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
                    gas_fee=f"{gas_fee_eth:.8f}"
                ))
            except Exception as e:
                print(f"Error processing ETH transaction: {e}")
                continue
        
        # Process ERC-20 token transactions
        for tx in erc20_txs:
            try:
                decimals = int(tx.get("tokenDecimal", "18"))
                amount = int(tx.get("value", "0")) / (10 ** decimals)
                
                direction = "outgoing" if tx["from"].lower() == address.lower() else "incoming"
                
                gas_used = int(tx.get("gasUsed", "0"))
                gas_price = int(tx.get("gasPrice", "0"))
                gas_fee_eth = (gas_used * gas_price) / 1e18
                
                # Token symbol comes directly from smart contract
                # Some tokens may have unusual names - this is actual blockchain data
                token_symbol = tx.get("tokenSymbol", "UNKNOWN")
                
                transactions.append(Transaction(
                    hash=tx["hash"],
                    network="ERC",
                    timestamp=int(tx["timeStamp"]),
                    datetime=datetime.utcfromtimestamp(int(tx["timeStamp"])).strftime("%Y-%m-%d %H:%M:%S"),
                    **{"from": tx["from"], "to": tx["to"]},
                    amount=f"{amount:.8f}",
                    token_symbol=token_symbol,
                    token_contract=tx.get("contractAddress"),
                    direction=direction,
                    status="Success",
                    block_number=int(tx["blockNumber"]),
                    gas_fee=f"{gas_fee_eth:.8f}"
                ))
            except Exception as e:
                print(f"Error processing ERC-20 transaction: {e}")
                continue
        
        # Sort by timestamp descending
        transactions.sort(key=lambda x: x.timestamp, reverse=True)
        
        logger.info(f"Total Ethereum transactions processed: {len(transactions)}")
        return transactions


# Global service instance
etherscan_service = EtherscanService()
