"""AES-GCM encryption for sender wallet private keys.

The Key-Encryption-Key (KEK) is loaded from `settings.airdrop_kek` (a base64
encoded 32-byte value). All ciphertexts use a fresh 12-byte nonce stored
alongside the ciphertext in the database.
"""
from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.config import settings


class CryptoUnavailable(RuntimeError):
    """Raised when AIRDROP_KEK is not configured."""


@lru_cache(maxsize=1)
def _kek() -> bytes:
    raw = settings.airdrop_kek.strip() if settings.airdrop_kek else ""
    if not raw:
        raise CryptoUnavailable(
            "AIRDROP_KEK is not configured. Set a base64-encoded 32-byte key "
            "in your .env file before adding wallets."
        )
    try:
        key = base64.b64decode(raw)
    except Exception as e:  # noqa: BLE001
        raise CryptoUnavailable(f"AIRDROP_KEK is not valid base64: {e}") from e
    if len(key) != 32:
        raise CryptoUnavailable(
            f"AIRDROP_KEK must decode to 32 bytes (got {len(key)})."
        )
    return key


def encrypt_private_key(private_key_hex: str) -> tuple[bytes, bytes]:
    """Encrypt a hex-encoded private key. Returns ``(ciphertext, nonce)``."""
    pk = private_key_hex.strip()
    if pk.startswith("0x") or pk.startswith("0X"):
        pk = pk[2:]
    if len(pk) != 64:
        raise ValueError("Private key must be a 64-char hex string (32 bytes).")
    try:
        plaintext = bytes.fromhex(pk)
    except ValueError as e:
        raise ValueError(f"Private key is not valid hex: {e}") from e

    aes = AESGCM(_kek())
    nonce = os.urandom(12)
    ciphertext = aes.encrypt(nonce, plaintext, associated_data=None)
    return ciphertext, nonce


def decrypt_private_key(ciphertext: bytes, nonce: bytes) -> str:
    """Decrypt a stored key. Returns a 0x-prefixed hex string."""
    aes = AESGCM(_kek())
    plaintext = aes.decrypt(bytes(nonce), bytes(ciphertext), associated_data=None)
    return "0x" + plaintext.hex()
