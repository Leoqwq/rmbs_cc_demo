"""TEE compute enclave (FastAPI).

GET  /enclave_pubkey -> the enclave's Umbral receiving public key (base64).
POST /compute        -> decrypt the re-encrypted inputs inside the enclave, run the
                        waterfall, return result + keccak hash + a TEE signature BOUND
                        to the request (id + ciphertextHash + resultHash).
GET  /tee_address    -> the enclave's signing address.
"""
from fastapi import FastAPI
from pydantic import BaseModel

import abi_digest as ad
from tee.compute import compute_waterfall
from tee.enclave_keys import load_or_create_enclave_key
from tee.encryption_seam import decrypt_inputs
from tee.signing import load_or_create_key, result_hash, sign_request_bound
from umbral_io import b64d, b64e, load_public_state

app = FastAPI(title="RMBS Confidential Compute TEE")

PRIVATE_KEY, TEE_ADDRESS = load_or_create_key()
print(f"TEE signing address: {TEE_ADDRESS}")
print("Set this as TEE_ADDRESS in .env (and pass it to deploy) before deploying.")


class ComputeRequest(BaseModel):
    id: int
    capsule: str
    ciphertext: str
    cfrags: list[str]


@app.get("/tee_address")
def tee_address():
    return {"success": True, "address": TEE_ADDRESS}


@app.get("/enclave_pubkey")
def enclave_pubkey():
    _, pk = load_or_create_enclave_key()
    return {"success": True, "pubkey": b64e(bytes(pk))}


@app.post("/compute")
def compute(req: ComputeRequest):
    try:
        enclave_sk, _ = load_or_create_enclave_key()
        state = load_public_state()
        inp = decrypt_inputs(req.capsule, req.ciphertext, req.cfrags, enclave_sk, state)
        result = compute_waterfall(
            iaf=float(inp["iaf"]), paf=float(inp["paf"]), period=int(inp["period"]))
        h = result_hash(result)
        ch = ad.ciphertext_hash(b64d(req.capsule), b64d(req.ciphertext))
        tee_sig = sign_request_bound(req.id, ch, h, PRIVATE_KEY)
        return {
            "success": True,
            "result": result,
            "resultHash": h.hex(),
            "teeSig": "0x" + tee_sig.hex(),
            "teeAddress": TEE_ADDRESS,
        }
    except Exception as e:  # noqa: BLE001 - surface to caller for the demo
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
