from eth_account import Account
from web3 import Web3

import abi_digest as ad


def test_tee_digest_matches_manual_abi_encode():
    from eth_abi import encode
    rh = Web3.keccak(text='{"x":1}')
    expected = Web3.keccak(
        encode(["uint256", "string", "uint256", "uint256", "uint256", "bytes32"],
               [1, "TEST_SEQ_2024", 1, 500000, 1000000, rh])
    )
    assert ad.tee_digest(1, "TEST_SEQ_2024", 1, 500000, 1000000, rh) == expected


def test_oracle_digest_matches_manual_abi_encode():
    from eth_abi import encode
    rh = Web3.keccak(text='{"x":1}')
    expected = Web3.keccak(encode(["uint256", "bytes32"], [1, rh]))
    assert ad.oracle_digest(1, rh) == expected


def test_sign_and_recover_roundtrip():
    pk = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    digest = Web3.keccak(text="hello")
    sig = ad.sign_digest(digest, pk)
    assert ad.recover_digest(digest, sig) == Account.from_key(pk).address


import os


def test_tee_endpoint_signs_request_bound_digest():
    os.environ["TEE_PRIVATE_KEY"] = (
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    )
    from fastapi.testclient import TestClient
    from tee.tee_service import app
    from tee.signing import result_hash
    import abi_digest as ad

    client = TestClient(app)
    resp = client.post("/compute", json={
        "id": 1, "dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000,
    })
    body = resp.json()
    assert body["success"] is True
    rh = result_hash(body["result"])
    assert body["resultHash"] == rh.hex()

    digest = ad.tee_digest(1, "TEST_SEQ_2024", 1, 500000, 1000000, rh)
    sig = bytes.fromhex(body["teeSig"][2:])
    assert ad.recover_digest(digest, sig) == body["teeAddress"]
