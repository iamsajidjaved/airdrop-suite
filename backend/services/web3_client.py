"""Async Web3 client singleton + ERC-20 helpers.

All RPC traffic for the distribution feature flows through this module so that
configuration, retries and ABI access live in one place.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from functools import lru_cache
from typing import Optional

from web3 import AsyncHTTPProvider, AsyncWeb3
from web3.types import TxParams

from backend.config import settings

logger = logging.getLogger(__name__)


class Web3Unavailable(RuntimeError):
    """Raised when ETH_RPC_URL is not configured."""


# Minimal ERC-20 ABI: just the calls the distributor needs.
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]


@lru_cache(maxsize=1)
def get_web3() -> AsyncWeb3:
    url = (settings.eth_rpc_url or "").strip()
    if not url:
        raise Web3Unavailable(
            "ETH_RPC_URL is not configured. Set it in .env to enable wallet "
            "balance lookups and the distribution worker."
        )
    return AsyncWeb3(AsyncHTTPProvider(url, request_kwargs={"timeout": 10}))


def to_checksum(address: str) -> str:
    return AsyncWeb3.to_checksum_address(address)


def token_units(amount: Decimal, decimals: int) -> int:
    """Convert a human-readable amount to integer base units."""
    q = Decimal(10) ** decimals
    return int((Decimal(amount) * q).to_integral_value())


def from_token_units(amount: int, decimals: int) -> Decimal:
    q = Decimal(10) ** decimals
    return Decimal(amount) / q


async def get_eth_balance(address: str) -> Decimal:
    w3 = get_web3()
    wei = await w3.eth.get_balance(to_checksum(address))
    return Decimal(wei) / Decimal(10**18)


async def get_token_balance(token_address: str, holder: str, decimals: int) -> Decimal:
    w3 = get_web3()
    contract = w3.eth.contract(address=to_checksum(token_address), abi=ERC20_ABI)
    raw = await contract.functions.balanceOf(to_checksum(holder)).call()
    return from_token_units(int(raw), decimals)


async def build_transfer_tx(
    *,
    token_address: str,
    sender: str,
    recipient: str,
    amount_units: int,
    nonce: int,
    max_fee_wei: int,
    max_priority_wei: int,
    chain_id: int,
    gas_limit: Optional[int] = None,
) -> TxParams:
    w3 = get_web3()
    contract = w3.eth.contract(address=to_checksum(token_address), abi=ERC20_ABI)
    fn = contract.functions.transfer(to_checksum(recipient), int(amount_units))

    tx: TxParams = {
        "from": to_checksum(sender),
        "nonce": int(nonce),
        "maxFeePerGas": int(max_fee_wei),
        "maxPriorityFeePerGas": int(max_priority_wei),
        "chainId": int(chain_id),
        "type": 2,
    }
    if gas_limit is None:
        try:
            gas_limit = await fn.estimate_gas({"from": to_checksum(sender)})
            # Add a 20% safety margin.
            gas_limit = int(gas_limit * 12 // 10)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                f"Gas estimation failed for transfer to {recipient} — the contract may "
                f"not be deployed on this network, or the sender has insufficient balance. "
                f"Original error: {e}"
            ) from e
    tx["gas"] = int(gas_limit)
    return await fn.build_transaction(tx)


async def estimate_fees() -> tuple[int, int]:
    """Return ``(max_fee_wei, max_priority_wei)`` using current base fee.

    `max_fee = base_fee * 2 + tip`, capped by `distribution_max_gas_price_gwei`.
    """
    w3 = get_web3()
    block = await w3.eth.get_block("pending")
    base_fee = int(block.get("baseFeePerGas") or 0)
    try:
        tip = int(await w3.eth.max_priority_fee)
    except Exception:  # noqa: BLE001
        tip = w3.to_wei(2, "gwei")
    cap_wei = w3.to_wei(settings.distribution_max_gas_price_gwei, "gwei")
    max_fee = min(base_fee * 2 + tip, cap_wei)
    if max_fee < tip:
        # base_fee == 0 (testnet) — fall back to tip.
        max_fee = tip
    return max_fee, tip


async def get_revert_reason(tx_hash: str, w3: AsyncWeb3) -> str:
    """Attempt to replay a reverted transaction to extract the revert reason.

    Returns a human-readable string. Never raises — falls back to a plain
    description if the RPC doesn't support debug_traceTransaction or the
    replay fails.
    """
    try:
        receipt = await w3.eth.get_transaction_receipt(tx_hash)
        tx = await w3.eth.get_transaction(tx_hash)
        # eth_call at the mined block replays the transaction and raises with
        # the revert message encoded in the exception.
        await w3.eth.call(
            {
                "to": tx["to"],
                "from": tx["from"],
                "data": tx["input"],
                "value": tx["value"],
                "gas": tx["gas"],
            },
            receipt["blockNumber"],
        )
        # If eth_call succeeds here the revert was likely a gas issue.
        return "transaction reverted (gas exhaustion or state change between blocks)"
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        # web3.py wraps revert reasons in ContractLogicError; try to surface
        # the inner message cleanly.
        if "execution reverted" in msg.lower():
            # Strip the surrounding "ContractLogicError: execution reverted: " prefix.
            for prefix in ("ContractLogicError: ", "execution reverted: ", "execution reverted"):
                msg = msg.replace(prefix, "").strip()
        return msg or "unknown revert reason"
