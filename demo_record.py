"""Wait for a compute request to finalize, print the result once, and archive it.

Used by `make demo` so a run is self-contained: no manual `read_result.py <id>` needed,
and every run leaves a timestamped JSON under demo-results/ for later lookup.

  python demo_record.py <id> [--iaf N --paf N] [--timeout 90] [--out-dir demo-results]
"""
import argparse
import json
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from web3 import Web3

from chain import connect_web3, get_rpc_urls

ABI_PATH = os.path.join(os.path.dirname(__file__), "out",
                        "ConfidentialCompute.sol", "ConfidentialCompute.json")


def load_contract():
    w3 = connect_web3(get_rpc_urls())
    with open(ABI_PATH, encoding="utf-8") as f:
        abi = json.load(f)["abi"]
    return w3.eth.contract(
        address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]), abi=abi)


def fetch(contract, request_id):
    finalized, count, result_hash, result_json = \
        contract.functions.getResult(request_id).call()
    threshold = contract.functions.threshold().call()
    return finalized, count, threshold, result_hash, result_json


def wait_for_final(contract, request_id, timeout, interval=3):
    """Poll getResult until finalized or `timeout` seconds elapse; return the last state."""
    deadline = time.time() + timeout
    while True:
        state = fetch(contract, request_id)
        if state[0] or time.time() >= deadline:
            return state
        time.sleep(interval)


def render(request_id, finalized, count, threshold, result_hash, result_json):
    lines = [
        f"request id : {request_id}",
        f"finalized  : {finalized}  (attestations {count}/{threshold} DON quorum)",
        f"resultHash : 0x{result_hash.hex()}",
    ]
    if finalized and result_json:
        lines.append("result     :")
        lines.append(json.dumps(json.loads(result_json), indent=2))
    return "\n".join(lines)


def archive(out_dir, request_id, finalized, count, threshold, result_hash, result_json,
            iaf=None, paf=None):
    """Write a self-describing JSON record; return its path. Filename sorts chronologically."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(out_dir, f"{stamp}-req{request_id}.json")
    record = {
        "request_id": request_id,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {"iaf": iaf, "paf": paf},
        "finalized": finalized,
        "attestations": count,
        "threshold": threshold,
        "result_hash": "0x" + result_hash.hex(),
        "result": json.loads(result_json) if result_json else None,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("id", type=int, help="request id from submit_request.py")
    p.add_argument("--iaf", type=int, help="recorded in the archive for context")
    p.add_argument("--paf", type=int, help="recorded in the archive for context")
    p.add_argument("--timeout", type=int, default=90, help="max seconds to wait for quorum")
    p.add_argument("--out-dir", default="demo-results")
    a = p.parse_args(argv)

    load_dotenv()
    contract = load_contract()
    print(f"waiting for the DON to finalize id={a.id} (async attestation; up to {a.timeout}s)...")
    finalized, count, threshold, result_hash, result_json = \
        wait_for_final(contract, a.id, a.timeout)

    print(render(a.id, finalized, count, threshold, result_hash, result_json))
    path = archive(a.out_dir, a.id, finalized, count, threshold, result_hash, result_json,
                   iaf=a.iaf, paf=a.paf)
    print(f"\narchived → {path}")
    if not finalized:
        print(f"(not finalized within {a.timeout}s — check .run/oracles.log)")
    return 0 if finalized else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
