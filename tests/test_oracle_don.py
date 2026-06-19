from eth_account import Account
from web3 import Web3

import abi_digest as ad


def test_tee_digest_matches_manual_abi_encode():
    from eth_abi import encode
    rh = Web3.keccak(text='{"x":1}')
    ch = Web3.keccak(b"\xaa\xbb\xcc\xdd")
    expected = Web3.keccak(encode(["uint256", "bytes32", "bytes32"], [1, ch, rh]))
    assert ad.tee_digest(1, ch, rh) == expected


def test_ciphertext_hash_is_keccak_of_raw_concat():
    # Must match Solidity keccak256(abi.encodePacked(capsule, ciphertext)).
    assert ad.ciphertext_hash(b"\xaa\xbb", b"\xcc\xdd") == Web3.keccak(b"\xaa\xbb\xcc\xdd")


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
    import os
    from fastapi.testclient import TestClient
    from tests._umbral_helpers import setup_encrypted_request
    from tee.signing import result_hash
    import abi_digest as ad
    from umbral_io import b64d

    payload = {"dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000}
    s = setup_encrypted_request(payload, shares=3, threshold=2)
    os.environ["TEE_PRIVATE_KEY"] = (
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    )
    os.environ["ENCLAVE_ENC_SECRET"] = s["enclave_secret_b64"]
    os.environ["UMBRAL_STATE"] = s["state_path"]

    from tee.tee_service import app
    client = TestClient(app)
    resp = client.post("/compute", json={
        "id": 1, "capsule": s["capsule_b64"],
        "ciphertext": s["ciphertext_b64"], "cfrags": s["cfrags_b64"],
    })
    body = resp.json()
    assert body["success"] is True
    rh = result_hash(body["result"])
    ch = ad.ciphertext_hash(b64d(s["capsule_b64"]), b64d(s["ciphertext_b64"]))
    digest = ad.tee_digest(1, ch, rh)
    sig = bytes.fromhex(body["teeSig"][2:])
    assert ad.recover_digest(digest, sig) == body["teeAddress"]
