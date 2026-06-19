"""Shared digest + signature helpers for the oracle DON.

Digests are computed with eth_abi so they match the contract's abi.encode:
- tee_digest:    keccak256(abi.encode(id, ciphertextHash, resultHash))
                 where ciphertextHash = keccak256(capsule || ciphertext)
                 -> binds the enclave result to the exact submitted ciphertext.
- oracle_digest: keccak256(abi.encode(id, resultHash))
                 -> what each oracle signs to attest the (verified) result.

Both are signed/recovered with the Ethereum personal-sign prefix (EIP-191),
matching the contract's "\\x19Ethereum Signed Message:\\n32" handling.
"""
from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3


def ciphertext_hash(capsule: bytes, ciphertext: bytes) -> bytes:
    """keccak256(capsule || ciphertext) — matches Solidity
    keccak256(abi.encodePacked(capsule, ciphertext)) (raw byte concat)."""
    return Web3.keccak(bytes(capsule) + bytes(ciphertext))


def tee_digest(id: int, ciphertext_hash_bytes: bytes, result_hash: bytes) -> bytes:
    return Web3.keccak(
        encode(
            ["uint256", "bytes32", "bytes32"],
            [int(id), bytes(ciphertext_hash_bytes), bytes(result_hash)],
        )
    )


def oracle_digest(id: int, result_hash: bytes) -> bytes:
    return Web3.keccak(encode(["uint256", "bytes32"], [int(id), bytes(result_hash)]))


def sign_digest(digest: bytes, private_key: str) -> bytes:
    signed = Account.sign_message(encode_defunct(primitive=digest), private_key)
    return bytes(signed.signature)


def recover_digest(digest: bytes, signature: bytes) -> str:
    return Account.recover_message(encode_defunct(primitive=digest), signature=signature)
