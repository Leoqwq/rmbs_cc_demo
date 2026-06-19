import os

from umbral import SecretKey

from tee.enclave_keys import load_or_create_enclave_key
from umbral_io import b64e, b64d


def test_load_enclave_key_from_env_is_deterministic():
    sk = SecretKey.random()
    os.environ["ENCLAVE_ENC_SECRET"] = b64e(sk.to_secret_bytes())
    got_sk, got_pk = load_or_create_enclave_key()
    assert got_sk.to_secret_bytes() == sk.to_secret_bytes()
    assert bytes(got_pk) == bytes(sk.public_key())
    del os.environ["ENCLAVE_ENC_SECRET"]
