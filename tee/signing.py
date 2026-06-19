"""TEE signing: canonical serialization, keccak hashing, ECDSA over the hash.

The TEE signs keccak256(abi.encode(id, ciphertextHash, resultHash)) with
the Ethereum personal-sign prefix (EIP-191), binding the result to the exact
submitted ciphertext (inputs stay encrypted). The contract recovers the same
way and checks it equals the configured TEE address.
Key handling mirrors ccc-demo: load from a file or generate-and-persist on first run.
"""
import json
import os
from typing import Any, Dict

from eth_account import Account
from web3 import Web3

import abi_digest as ad

TEE_KEY_FILE = os.path.join(os.path.dirname(__file__), "kd", "tee_signing_key.json")


def canonical_json(result: Dict[str, Any]) -> str:
    """Deterministic JSON string the hash is computed over."""
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def result_hash(result: Dict[str, Any]) -> bytes:
    """keccak256 of the canonical JSON bytes (matches Solidity keccak256(bytes))."""
    return Web3.keccak(text=canonical_json(result))


def sign_request_bound(id: int, ciphertext_hash_bytes: bytes,
                       result_hash_bytes: bytes, private_key: str) -> bytes:
    """Sign keccak256(abi.encode(id, ciphertextHash, resultHash)) — binds the
    enclave result to the exact submitted ciphertext (inputs stay encrypted)."""
    digest = ad.tee_digest(id, ciphertext_hash_bytes, result_hash_bytes)
    return ad.sign_digest(digest, private_key)


def get_signer(private_key: str) -> str:
    """Ethereum address for a private key."""
    return Account.from_key(private_key).address


def load_or_create_key() -> tuple[str, str]:
    """Return (private_key, address). Use TEE_PRIVATE_KEY env if set, else a
    persisted file under tee/kd/, generating a fresh key on first run."""
    env_pk = os.getenv("TEE_PRIVATE_KEY")
    if env_pk:
        if not env_pk.startswith("0x"):
            env_pk = "0x" + env_pk
        return env_pk, get_signer(env_pk)

    if os.path.exists(TEE_KEY_FILE):
        with open(TEE_KEY_FILE) as f:
            data = json.load(f)
        return data["private_key"], data["address"]

    acct = Account.create()
    pk = acct.key.hex()
    if not pk.startswith("0x"):
        pk = "0x" + pk
    os.makedirs(os.path.dirname(TEE_KEY_FILE), exist_ok=True)
    with open(TEE_KEY_FILE, "w") as f:
        json.dump({"private_key": pk, "address": acct.address}, f, indent=2)
    return pk, acct.address
