import os
import tempfile

import pytest
from umbral import (SecretKey, VerifiedKeyFrag, encrypt, reencrypt,
                    decrypt_reencrypted)

import umbral_io as uio
from keygen import run_keygen


def _state_file():
    fd, path = tempfile.mkstemp(suffix="_state.json")
    os.close(fd)
    return path


def test_run_keygen_produces_threshold_recoverable_state():
    enclave_sk = SecretKey.random()
    path = _state_file()
    run_keygen(enclave_sk.public_key(), shares=3, threshold=2, out_path=path)

    state = uio.load_public_state(path)
    assert state["threshold"] == 2
    kfrags = uio.load_kfrags(path)
    assert len(kfrags) == 3

    capsule, ciphertext = encrypt(state["master_pk"], b'{"iaf":7}')
    vkfrags = [VerifiedKeyFrag.from_verified_bytes(k) for k in kfrags[:2]]
    cfrags = [reencrypt(capsule=capsule, kfrag=k) for k in vkfrags]
    plain = decrypt_reencrypted(
        receiving_sk=enclave_sk, delegating_pk=state["master_pk"],
        capsule=capsule, verified_cfrags=cfrags, ciphertext=ciphertext)
    assert plain == b'{"iaf":7}'


def test_below_threshold_cannot_decrypt():
    enclave_sk = SecretKey.random()
    path = _state_file()
    run_keygen(enclave_sk.public_key(), shares=3, threshold=2, out_path=path)
    state = uio.load_public_state(path)
    capsule, ciphertext = encrypt(state["master_pk"], b'{"iaf":7}')
    one = [VerifiedKeyFrag.from_verified_bytes(uio.load_kfrags(path)[0])]
    cfrags = [reencrypt(capsule=capsule, kfrag=k) for k in one]
    with pytest.raises(Exception):
        decrypt_reencrypted(
            receiving_sk=enclave_sk, delegating_pk=state["master_pk"],
            capsule=capsule, verified_cfrags=cfrags, ciphertext=ciphertext)
