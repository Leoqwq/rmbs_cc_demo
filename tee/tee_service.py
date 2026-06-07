"""TEE compute enclave (FastAPI).

POST /compute  -> decrypt inputs (identity for now), run the waterfall, return the
                  result + keccak hash + a TEE signature BOUND to the request
                  (id + inputs + resultHash). Oracle nodes verify this signature.
GET  /tee_address -> the enclave's signing address.
"""
from fastapi import FastAPI
from pydantic import BaseModel

from tee.compute import compute_waterfall
from tee.encryption_seam import decrypt_inputs
from tee.signing import load_or_create_key, result_hash, sign_request_bound

app = FastAPI(title="RMBS Confidential Compute TEE")

PRIVATE_KEY, TEE_ADDRESS = load_or_create_key()
print(f"TEE signing address: {TEE_ADDRESS}")
print("Set this as TEE_ADDRESS in .env (and pass it to deploy) before deploying.")


class ComputeRequest(BaseModel):
    id: int
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
        inp = decrypt_inputs({"iaf": req.iaf, "paf": req.paf, "period": req.period})
        result = compute_waterfall(iaf=float(inp["iaf"]), paf=float(inp["paf"]), period=inp["period"])
        h = result_hash(result)
        tee_sig = sign_request_bound(req.id, req.dealId, req.period, req.iaf, req.paf, h, PRIVATE_KEY)
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
