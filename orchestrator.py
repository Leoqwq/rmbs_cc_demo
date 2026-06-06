"""Orchestrator (Oracle role): watch ComputeRequested, call the TEE, post results.

Plaintext pipeline — no decryption. For each new on-chain request it forwards
{dealId, period, iaf, paf} to the TEE, receives the result + TEE signature, and
sends postResult() back to the contract (signed by the funded deployer account).

Robustness:
- **RPC failover** (#2): connects to any of RPC_URLS; on a transport error it
  fails over to the next validator endpoint (see chain.ResilientChain).
- **Idempotent + resumable** (#3): progress (last scanned block + completed
  request ids) is persisted to orchestrator_state.json. On restart it resumes
  from the last scanned block (default: genesis), and before doing work it checks
  the contract's own getResult() so a request is never computed/posted twice. A
  request that fails (TEE down, reverted tx) is left un-advanced and retried on
  the next poll — so an outage anywhere stalls but never loses the request.
"""
import json
import os
import time

import requests
from dotenv import load_dotenv

from chain import ResilientChain, TRANSPORT_ERRORS, get_rpc_urls

POLL_INTERVAL = 3
STATE_FILE = os.path.join(os.path.dirname(__file__), "orchestrator_state.json")
ABI_PATH = os.path.join(
    os.path.dirname(__file__), "out", "ConfidentialCompute.sol", "ConfidentialCompute.json"
)


def load_abi():
    with open(ABI_PATH) as f:
        return json.load(f)["abi"]


def load_state(path: str = STATE_FILE):
    """Return (last_scanned_block, completed_ids set). Defaults: 0, empty."""
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        return int(data.get("last_scanned_block", 0)), set(data.get("completed_ids", []))
    return 0, set()


def save_state(last_block: int, completed_ids, path: str = STATE_FILE):
    """Atomically persist progress so a restart resumes without re-doing work."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(
            {"last_scanned_block": last_block, "completed_ids": sorted(completed_ids)},
            f,
            indent=2,
        )
    os.replace(tmp, path)


def _already_posted(chain: ResilientChain, request_id: int) -> bool:
    posted, _, _ = chain.run(lambda w3, c: c.functions.getResult(request_id).call())
    return posted


def _post_result(chain: ResilientChain, admin_pk: str, admin_address: str,
                 chain_id: int, request_id: int, body: dict):
    result_hash = bytes.fromhex(body["resultHash"])
    result_json = json.dumps(body["result"], sort_keys=True, separators=(",", ":"))
    sig = bytes.fromhex(body["signature"][2:])

    def _send(w3, contract):
        tx = contract.functions.postResult(
            request_id, result_hash, result_json, sig
        ).build_transaction(
            {
                "from": admin_address,
                "nonce": w3.eth.get_transaction_count(admin_address),
                "gas": 800000,
                "gasPrice": 0,
                "chainId": chain_id,
            }
        )
        signed = w3.eth.account.sign_transaction(tx, admin_pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        return tx_hash, receipt

    return chain.run(_send)


def handle_request(chain, tee_url, admin_pk, admin_address, chain_id,
                   request_id, args, completed_ids) -> bool:
    """Process one request idempotently. Returns True if it is now done.

    Raises TRANSPORT_ERRORS only for chain-side outages (handled by the caller);
    TEE outages are caught here and reported as not-done so the loop retries.
    """
    if request_id in completed_ids:
        return True
    if _already_posted(chain, request_id):
        print(f"  request {request_id}: result already on-chain; marking done")
        completed_ids.add(request_id)
        return True

    print(f"\n>>> ComputeRequested id={request_id} deal={args['dealId']} "
          f"period={args['period']} IAF={args['iaf']} PAF={args['paf']}")
    print("  forwarding to TEE...")
    try:
        resp = requests.post(
            f"{tee_url}/compute",
            json={
                "dealId": args["dealId"],
                "period": args["period"],
                "iaf": args["iaf"],
                "paf": args["paf"],
            },
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as e:
        print(f"  TEE unreachable ({e}); will retry next loop")
        return False

    if not body.get("success"):
        print(f"  TEE error: {body.get('error')}; will retry next loop")
        return False
    print(f"  TEE result: {body['result']}")

    print("  posting signed result on-chain...")
    tx_hash, receipt = _post_result(chain, admin_pk, admin_address, chain_id, request_id, body)
    if receipt["status"] == 1:
        print(f"  postResult ok tx={tx_hash.hex()}")
        completed_ids.add(request_id)
        return True

    # status 0: a prior attempt may have already landed — confirm via the chain.
    if _already_posted(chain, request_id):
        completed_ids.add(request_id)
        return True
    print(f"  postResult reverted (status 0); will retry next loop")
    return False


def main():
    load_dotenv()
    chain_id = int(os.environ["CHAIN_ID"])
    contract_address = os.environ["CONTRACT_ADDRESS"]
    tee_url = os.environ["TEE_URL"].rstrip("/")
    admin_pk = os.environ["DEPLOYER_PRIVATE_KEY"]

    chain = ResilientChain(get_rpc_urls(), contract_address, load_abi())
    admin_address = chain.w3.eth.account.from_key(admin_pk).address

    last_block, completed_ids = load_state()
    print(f"Orchestrator up. chain_id={chain.w3.eth.chain_id} contract={chain.contract_address}")
    print(f"Admin={admin_address}  TEE={tee_url}  RPCs={chain.rpc_urls}")
    print(f"Resuming from block {last_block}, {len(completed_ids)} requests already completed.")

    while True:
        try:
            current = chain.run(lambda w3, c: w3.eth.block_number)
            if current > last_block:
                events = chain.run(
                    lambda w3, c: c.events.ComputeRequested.create_filter(
                        from_block=last_block + 1, to_block=current
                    ).get_all_entries()
                )
                all_done = True
                for event in events:
                    done = handle_request(
                        chain, tee_url, admin_pk, admin_address, chain_id,
                        event["args"]["id"], event["args"], completed_ids,
                    )
                    if not done:
                        all_done = False
                        break  # retry this block range next loop (idempotent)
                # Always persist completed ids; only advance the cursor if the
                # whole range succeeded, otherwise re-scan it next time.
                if all_done:
                    last_block = current
                save_state(last_block, completed_ids)
        except TRANSPORT_ERRORS as e:
            print(f"  all RPC endpoints unavailable ({e}); retrying in {POLL_INTERVAL}s")
        except Exception as e:  # noqa: BLE001 - keep the loop alive for the demo
            print(f"  unexpected error: {e}; retrying in {POLL_INTERVAL}s")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
