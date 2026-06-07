import json
from tee.compute import compute_waterfall


def test_compute_waterfall_canonical_inputs():
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)

    assert result["period"] == 1
    assert result["bonds"]["ClassA"]["current_balance"] == 79000000.00
    assert result["bonds"]["ClassB"]["current_balance"] == 15000000.00
    assert result["bonds"]["ClassC"]["current_balance"] == 5000000.00
    assert result["bonds"]["ClassA"]["interest_shortfall"] == 0.0
    assert result["cash_remaining"]["IAF"] == 70833.33
    assert result["cash_remaining"]["PAF"] == 0.0


def test_compute_waterfall_is_deterministic():
    a = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    b = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


from web3 import Web3

from tee.compute import compute_waterfall
from tee.signing import canonical_json, result_hash


def test_canonical_json_is_sorted_and_compact():
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    s = canonical_json(result)
    assert s == json.dumps(result, sort_keys=True, separators=(",", ":"))


def test_result_hash_matches_web3_keccak_of_canonical_json():
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    s = canonical_json(result)
    assert result_hash(result) == Web3.keccak(text=s)


import os
from fastapi.testclient import TestClient


def test_compute_endpoint_matches_pure_function_and_verifies_signature():
    os.environ["TEE_PRIVATE_KEY"] = (
        "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    )
    from tee.tee_service import app
    import abi_digest as ad

    client = TestClient(app)
    resp = client.post("/compute", json={
        "id": 1, "dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True

    expected = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    assert body["result"] == expected
    rh = result_hash(expected)
    assert body["resultHash"] == rh.hex()
    digest = ad.tee_digest(1, "TEST_SEQ_2024", 1, 500000, 1000000, rh)
    assert ad.recover_digest(digest, bytes.fromhex(body["teeSig"][2:])) == body["teeAddress"]
