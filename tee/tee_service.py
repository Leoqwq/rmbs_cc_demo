"""TEE compute enclave (FastAPI).

POST /compute  -> runs the waterfall, returns the result + keccak hash + TEE
                  signature over that hash.
GET  /tee_address -> the TEE's Ethereum signing address (for contract wiring).

Encryption is intentionally omitted in this demo: inputs/outputs are plaintext.
The signature is what lets the contract trust the result came from this TEE.
"""
from fastapi import FastAPI
from pydantic import BaseModel

from tee.compute import compute_waterfall
from tee.signing import load_or_create_key, result_hash, sign_result

app = FastAPI(title="RMBS Confidential Compute TEE")

PRIVATE_KEY, TEE_ADDRESS = load_or_create_key()
print(f"TEE signing address: {TEE_ADDRESS}")
print("Set this as TEE_ADDRESS in .env before deploying the contract.")


class ComputeRequest(BaseModel):
    dealId: str
    period: int
    iaf: int
    paf: int


@app.get("/tee_address")
def tee_address():
    return {"success": True, "address": TEE_ADDRESS}


@app.post("/compute")
def compute(req: ComputeRequest):
    try:
        result = compute_waterfall(iaf=float(req.iaf), paf=float(req.paf), period=req.period)
        h = result_hash(result)
        sig = sign_result(h, PRIVATE_KEY)
        return {
            "success": True,
            "result": result,
            "resultHash": h.hex(),
            "signature": "0x" + sig.hex(),
            "teeAddress": TEE_ADDRESS,
        }
    except Exception as e:  # noqa: BLE001 - surface error to caller for the demo
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
