"""One decryption-DON node (white-paper steps 3-4): a pure re-encryption capability.

Holds exactly one key fragment (KFRAG, base64) and, given a capsule, returns a
re-encryption fragment (cfrag). Stateless; no chain access; no funded account.
Set CORRUPTED=1 to flip a byte and demonstrate that the oracle's cfrag
verification rejects a faulty node while the quorum still succeeds.

Run one per node:
  KFRAG=<base64> uvicorn decryption_node:app --host 0.0.0.0 --port 5000
"""
import os

from fastapi import FastAPI
from pydantic import BaseModel
from umbral import Capsule, VerifiedKeyFrag, reencrypt

from umbral_io import b64d, b64e

app = FastAPI(title="RMBS Decryption Node")


class ReencryptRequest(BaseModel):
    capsule: str


def _load_kfrag() -> VerifiedKeyFrag:
    kfrag_b64 = os.environ["KFRAG"]
    return VerifiedKeyFrag.from_verified_bytes(b64d(kfrag_b64))


@app.post("/reencrypt")
def reencrypt_capsule(data: ReencryptRequest):
    kfrag = _load_kfrag()
    capsule = Capsule.from_bytes(b64d(data.capsule))
    cfrag = reencrypt(capsule=capsule, kfrag=kfrag)
    cfrag_bytes = bytes(cfrag)
    if os.getenv("CORRUPTED", "0") == "1":
        corrupted = bytearray(cfrag_bytes)
        corrupted[0] ^= 0xFF
        cfrag_bytes = bytes(corrupted)
    return {"cfrag": b64e(cfrag_bytes)}
