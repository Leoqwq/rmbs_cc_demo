"""Shared digest + signature helpers for the oracle DON.

Digests are computed with eth_abi so they match the contract's abi.encode:
- tee_digest:    keccak256(abi.encode(id, dealId, period, iaf, paf, resultHash))
                 -> binds the enclave result to the exact request + inputs.
- oracle_digest: keccak256(abi.encode(id, resultHash))
                 -> what each oracle signs to attest the (verified) result.

Both are signed/recovered with the Ethereum personal-sign prefix (EIP-191),
matching the contract's "\\x19Ethereum Signed Message:\\n32" handling.
"""
from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3


def tee_digest(id: int, deal_id: str, period: int, iaf: int, paf: int, result_hash: bytes) -> bytes:
    return Web3.keccak(
        encode(
            ["uint256", "string", "uint256", "uint256", "uint256", "bytes32"],
            [int(id), str(deal_id), int(period), int(iaf), int(paf), bytes(result_hash)],
        )
    )


def oracle_digest(id: int, result_hash: bytes) -> bytes:
    return Web3.keccak(encode(["uint256", "bytes32"], [int(id), bytes(result_hash)]))


def sign_digest(digest: bytes, private_key: str) -> bytes:
    signed = Account.sign_message(encode_defunct(primitive=digest), private_key)
    return bytes(signed.signature)


def recover_digest(digest: bytes, signature: bytes) -> str:
    return Account.recover_message(encode_defunct(primitive=digest), signature=signature)
