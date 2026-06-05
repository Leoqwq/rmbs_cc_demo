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


from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from tee.compute import compute_waterfall
from tee.signing import canonical_json, result_hash, sign_result, get_signer


def test_canonical_json_is_sorted_and_compact():
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    s = canonical_json(result)
    assert s == json.dumps(result, sort_keys=True, separators=(",", ":"))


def test_result_hash_matches_web3_keccak_of_canonical_json():
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    s = canonical_json(result)
    assert result_hash(result) == Web3.keccak(text=s)


def test_signature_recovers_to_signer_address():
    # deterministic test key (NOT used in production)
    pk = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
    result = compute_waterfall(iaf=500000.0, paf=1000000.0, period=1)
    h = result_hash(result)
    sig = sign_result(h, pk)
    recovered = Account.recover_message(encode_defunct(primitive=h), signature=sig)
    assert recovered == get_signer(pk)
