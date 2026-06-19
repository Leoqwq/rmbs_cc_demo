"""Shared pyUmbral glue used by the user CLI, oracle agent, decryption node,
keygen, and the TEE seam. All Umbral blobs cross process boundaries as base64.

Public state (kd/umbral_state.json) holds only PUBLIC material plus the per-node
key fragments; no master/enclave secret ever lives here.
"""
import base64
import json
import os

from umbral import Capsule, CapsuleFrag, PublicKey

DEFAULT_STATE = os.path.join(os.path.dirname(__file__), "kd", "umbral_state.json")


def b64e(b: bytes) -> str:
    return base64.b64encode(bytes(b)).decode("utf-8")


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))


def _state_path(path: str | None) -> str:
    return path or os.getenv("UMBRAL_STATE") or DEFAULT_STATE


def load_public_state(path: str | None = None) -> dict:
    """Load master/authority/enclave public keys + threshold from the state file."""
    with open(_state_path(path)) as f:
        d = json.load(f)
    return {
        "master_pk": PublicKey.from_bytes(b64d(d["master_public_key"])),
        "authority_pk": PublicKey.from_bytes(b64d(d["authority_public_key"])),
        "enclave_pk": PublicKey.from_bytes(b64d(d["enclave_public_key"])),
        "threshold": int(d["threshold"]),
    }


def load_kfrags(path: str | None = None) -> list[bytes]:
    """Raw (base64-decoded) kfrag bytes, one per node."""
    with open(_state_path(path)) as f:
        d = json.load(f)
    return [b64d(k) for k in d.get("kfrags", [])]


def verify_cfrags(capsule_b64: str, cfrag_b64_list: list[str], state: dict) -> list[str]:
    """Return the base64 cfrags that verify against the state's keys; drop the rest.

    A corrupt/lying decryption node yields a cfrag that fails verification — this is
    how the quorum tolerates it (mirrors ccc-demo's faulty-node demo)."""
    capsule = Capsule.from_bytes(b64d(capsule_b64))
    verified: list[str] = []
    for cb in cfrag_b64_list:
        try:
            cfrag = CapsuleFrag.from_bytes(b64d(cb))
            cfrag.verify(
                capsule,
                verifying_pk=state["authority_pk"],
                delegating_pk=state["master_pk"],
                receiving_pk=state["enclave_pk"],
            )
            verified.append(cb)
        except Exception:
            continue
    return verified
