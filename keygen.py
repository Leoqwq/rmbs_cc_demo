"""Trusted-dealer setup for the decryption DON (white-paper step 0).

Generates the master keypair (delegating), an authority signer (for cfrag
verification), and N key fragments bound to the enclave's receiving public key,
then writes kd/umbral_state.json (public material + kfrags). The master/authority
secrets are NOT persisted — a crude stand-in for "no single party holds the key"
(real threshold DKG is future work).

Usage (after the TEE is running):
  python keygen.py --shares 3 --threshold 2
"""
import argparse
import json
import os

import requests
from umbral import PublicKey, SecretKey, Signer, generate_kfrags

from umbral_io import DEFAULT_STATE, b64d, b64e


def run_keygen(enclave_pk: PublicKey, shares: int, threshold: int, out_path: str) -> dict:
    master_sk = SecretKey.random()
    authority_sk = SecretKey.random()
    kfrags = generate_kfrags(
        delegating_sk=master_sk,
        receiving_pk=enclave_pk,
        signer=Signer(authority_sk),
        threshold=threshold,
        shares=shares,
    )
    state = {
        "master_public_key": b64e(bytes(master_sk.public_key())),
        "authority_public_key": b64e(bytes(authority_sk.public_key())),
        "enclave_public_key": b64e(bytes(enclave_pk)),
        "threshold": threshold,
        "kfrags": [b64e(bytes(k)) for k in kfrags],
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(state, f, indent=2)
    return state


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tee-url", default=os.getenv("TEE_URL", "http://127.0.0.1:8000"))
    p.add_argument("--shares", type=int, required=True, help="N decryption nodes")
    p.add_argument("--threshold", type=int, required=True, help="m needed to decrypt")
    p.add_argument("--out", default=DEFAULT_STATE)
    a = p.parse_args()

    resp = requests.get(f"{a.tee_url.rstrip('/')}/enclave_pubkey", timeout=10)
    resp.raise_for_status()
    enclave_pk = PublicKey.from_bytes(b64d(resp.json()["pubkey"]))

    run_keygen(enclave_pk, a.shares, a.threshold, a.out)
    print(f"Wrote {a.out}: {a.shares} kfrags, threshold {a.threshold}, "
          f"enclave_pk pinned. Distribute kfrags[i] as KFRAG to node i.")


if __name__ == "__main__":
    main()
