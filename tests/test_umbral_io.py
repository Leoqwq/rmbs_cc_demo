import json
import os
import tempfile

from umbral import SecretKey

import umbral_io as uio


def test_b64_roundtrip():
    raw = b"\x00\x01\x02\xaa\xff"
    assert uio.b64d(uio.b64e(raw)) == raw


def test_load_public_state_parses_keys_and_threshold():
    master = SecretKey.random().public_key()
    authority = SecretKey.random().public_key()
    enclave = SecretKey.random().public_key()
    fd, path = tempfile.mkstemp(suffix="_state.json")
    os.close(fd)
    with open(path, "w") as f:
        json.dump({
            "master_public_key": uio.b64e(bytes(master)),
            "authority_public_key": uio.b64e(bytes(authority)),
            "enclave_public_key": uio.b64e(bytes(enclave)),
            "threshold": 2,
            "kfrags": [uio.b64e(b"k1"), uio.b64e(b"k2")],
        }, f)

    state = uio.load_public_state(path)
    assert bytes(state["master_pk"]) == bytes(master)
    assert bytes(state["authority_pk"]) == bytes(authority)
    assert bytes(state["enclave_pk"]) == bytes(enclave)
    assert state["threshold"] == 2
    assert uio.load_kfrags(path) == [b"k1", b"k2"]


def test_verify_cfrags_keeps_valid_drops_invalid_preserving_order():
    from umbral import SecretKey, Signer, encrypt, generate_kfrags, reencrypt

    master_sk = SecretKey.random()
    authority_sk = SecretKey.random()
    enclave_sk = SecretKey.random()
    state = {
        "master_pk": master_sk.public_key(),
        "authority_pk": authority_sk.public_key(),
        "enclave_pk": enclave_sk.public_key(),
        "threshold": 2,
    }
    capsule, _ = encrypt(master_sk.public_key(), b"payload")
    kfrags = generate_kfrags(
        delegating_sk=master_sk, receiving_pk=enclave_sk.public_key(),
        signer=Signer(authority_sk), threshold=2, shares=3)
    good = [uio.b64e(bytes(reencrypt(capsule=capsule, kfrag=k))) for k in kfrags[:2]]
    corrupt = uio.b64e(b"\x00" + uio.b64d(good[0])[1:])  # flip first byte
    capsule_b64 = uio.b64e(bytes(capsule))

    kept = uio.verify_cfrags(capsule_b64, [corrupt, good[0], good[1]], state)
    assert kept == good  # corrupt dropped; both valid kept; order preserved
