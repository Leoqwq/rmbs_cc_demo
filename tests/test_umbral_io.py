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
