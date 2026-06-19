import os

import pytest
from umbral import SecretKey

from tee.enclave_keys import load_or_create_enclave_key
from tee.encryption_seam import decrypt_inputs
from umbral_io import b64e, b64d, load_public_state
from tests._umbral_helpers import setup_encrypted_request

PAYLOAD = {"dealId": "TEST_SEQ_2024", "period": 1, "iaf": 500000, "paf": 1000000}


def test_load_enclave_key_from_env_is_deterministic():
    sk = SecretKey.random()
    os.environ["ENCLAVE_ENC_SECRET"] = b64e(sk.to_secret_bytes())
    got_sk, got_pk = load_or_create_enclave_key()
    assert got_sk.to_secret_bytes() == sk.to_secret_bytes()
    assert bytes(got_pk) == bytes(sk.public_key())
    del os.environ["ENCLAVE_ENC_SECRET"]


def test_decrypt_inputs_roundtrip():
    s = setup_encrypted_request(PAYLOAD, shares=3, threshold=2)
    state = load_public_state(s["state_path"])
    enclave_sk = SecretKey.from_bytes(b64d(s["enclave_secret_b64"]))
    out = decrypt_inputs(s["capsule_b64"], s["ciphertext_b64"], s["cfrags_b64"],
                         enclave_sk, state)
    assert out == PAYLOAD


def test_decrypt_inputs_drops_corrupt_cfrag_but_succeeds_at_threshold():
    s = setup_encrypted_request(PAYLOAD, shares=3, threshold=2)
    state = load_public_state(s["state_path"])
    enclave_sk = SecretKey.from_bytes(b64d(s["enclave_secret_b64"]))
    bad = b64e(b"\x00" + b64d(s["cfrags_b64"][0])[1:])  # flip first byte
    cfrags = [bad, s["cfrags_b64"][0], s["cfrags_b64"][1]]  # 1 bad + 2 good
    out = decrypt_inputs(s["capsule_b64"], s["ciphertext_b64"], cfrags, enclave_sk, state)
    assert out == PAYLOAD


def test_decrypt_inputs_raises_below_threshold():
    s = setup_encrypted_request(PAYLOAD, shares=3, threshold=2)
    state = load_public_state(s["state_path"])
    enclave_sk = SecretKey.from_bytes(b64d(s["enclave_secret_b64"]))
    with pytest.raises(ValueError, match="cfrags verified"):
        decrypt_inputs(s["capsule_b64"], s["ciphertext_b64"], s["cfrags_b64"][:1],
                       enclave_sk, state)
