"""One oracle node of the DON.

Per the white-paper response path, each oracle independently: watches the contract
for ComputeRequested, fetches the enclave's attested result from the TEE, VERIFIES
the TEE signature is bound to this exact request, signs its own attestation over
(id, resultHash), and submits attest() on-chain. The contract finalizes once an
m-of-n quorum of distinct oracles has attested (the DON-attested response).

Reuses chain.ResilientChain (RPC failover) and persists progress per oracle so a
restart resumes idempotently (the contract is the source of truth via getResult /
hasAttested). Run one instance per oracle, each with its own ORACLE_KEY.
"""
import json
import os
import time

import requests
from dotenv import load_dotenv

import abi_digest as ad
from chain import ResilientChain, TRANSPORT_ERRORS, get_rpc_urls

POLL_INTERVAL = 3
ABI_PATH = os.path.join(
    os.path.dirname(__file__), "out", "ConfidentialCompute.sol", "ConfidentialCompute.json"
)


def load_abi():
    with open(ABI_PATH) as f:
        return json.load(f)["abi"]


def state_path(oracle_id: str) -> str:
    return os.path.join(os.path.dirname(__file__), f"oracle_state_{oracle_id}.json")


def load_state(path: str):
    if os.path.exists(path):
        with open(path) as f:
            d = json.load(f)
        return int(d.get("last_scanned_block", 0)), set(d.get("attested_ids", []))
    return 0, set()


def save_state(last_block: int, attested_ids, path: str):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"last_scanned_block": last_block, "attested_ids": sorted(attested_ids)}, f, indent=2)
    os.replace(tmp, path)


def handle_request(chain, tee_url, tee_address, oracle_pk, oracle_address,
                   chain_id, request_id, args, attested_ids) -> bool:
    if request_id in attested_ids:
        return True
    finalized, _, _, _ = chain.run(lambda w3, c: c.functions.getResult(request_id).call())
    if finalized:
        attested_ids.add(request_id)
        return True
    already = chain.run(lambda w3, c: c.functions.hasAttested(request_id, oracle_address).call())
    if already:
        attested_ids.add(request_id)
        return True

    print(f"\n>>> [oracle {oracle_address}] ComputeRequested id={request_id} "
          f"IAF={args['iaf']} PAF={args['paf']}")
    try:
        resp = requests.post(f"{tee_url}/compute", json={
            "id": request_id, "dealId": args["dealId"], "period": args["period"],
            "iaf": args["iaf"], "paf": args["paf"],
        }, timeout=30)
        resp.raise_for_status()
        body = resp.json()
    except requests.RequestException as e:
        print(f"  TEE unreachable ({e}); retry next loop")
        return False
    if not body.get("success"):
        print(f"  TEE error: {body.get('error')}; retry next loop")
        return False

    result_hash = bytes.fromhex(body["resultHash"])
    tee_sig = bytes.fromhex(body["teeSig"][2:])

    # Verify the enclave attestation is bound to THIS request before signing.
    digest = ad.tee_digest(request_id, args["dealId"], args["period"], args["iaf"], args["paf"], result_hash)
    recovered = ad.recover_digest(digest, tee_sig)
    if recovered.lower() != tee_address.lower():
        print(f"  BAD TEE signature (got {recovered}, want {tee_address}); refusing to attest")
        return False

    result_json = json.dumps(body["result"], sort_keys=True, separators=(",", ":"))
    oracle_sig = ad.sign_digest(ad.oracle_digest(request_id, result_hash), oracle_pk)

    def _send(w3, contract):
        tx = contract.functions.attest(
            request_id, result_hash, result_json, tee_sig, oracle_sig
        ).build_transaction({
            "from": oracle_address,
            "nonce": w3.eth.get_transaction_count(oracle_address),
            "gas": 900000, "gasPrice": w3.eth.gas_price, "chainId": chain_id,
        })
        signed = w3.eth.account.sign_transaction(tx, oracle_pk)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        return h, w3.eth.wait_for_transaction_receipt(h)

    tx_hash, receipt = chain.run(_send)
    if receipt["status"] == 1:
        print(f"  attested ok tx={tx_hash.hex()}")
        attested_ids.add(request_id)
        return True
    # Someone may have finalized between our checks; treat as done if so.
    finalized, _, _, _ = chain.run(lambda w3, c: c.functions.getResult(request_id).call())
    if finalized or chain.run(lambda w3, c: c.functions.hasAttested(request_id, oracle_address).call()):
        attested_ids.add(request_id)
        return True
    print(f"  attest reverted (status 0); retry next loop")
    return False


def main():
    load_dotenv()
    oracle_id = os.environ["ORACLE_ID"]
    oracle_pk = os.environ["ORACLE_KEY"]
    chain_id = int(os.environ["CHAIN_ID"])
    contract_address = os.environ["CONTRACT_ADDRESS"]
    tee_url = os.environ["TEE_URL"].rstrip("/")
    tee_address = os.environ["TEE_ADDRESS"]

    chain = ResilientChain(get_rpc_urls(), contract_address, load_abi())
    oracle_address = chain.w3.eth.account.from_key(oracle_pk).address

    path = state_path(oracle_id)
    last_block, attested_ids = load_state(path)
    print(f"Oracle agent '{oracle_id}' up. addr={oracle_address} contract={chain.contract_address}")
    print(f"TEE={tee_url} (expect {tee_address})  RPCs={chain.rpc_urls}")
    print(f"Resuming from block {last_block}, {len(attested_ids)} requests already attested.")

    while True:
        try:
            current = chain.run(lambda w3, c: w3.eth.block_number)
            if current > last_block:
                events = chain.run(lambda w3, c: c.events.ComputeRequested.create_filter(
                    from_block=last_block + 1, to_block=current).get_all_entries())
                all_done = True
                for ev in events:
                    if not handle_request(chain, tee_url, tee_address, oracle_pk, oracle_address,
                                          chain_id, ev["args"]["id"], ev["args"], attested_ids):
                        all_done = False
                        break
                if all_done:
                    last_block = current
                save_state(last_block, attested_ids, path)
        except TRANSPORT_ERRORS as e:
            print(f"  RPC unavailable ({e}); retry in {POLL_INTERVAL}s")
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            print(f"  unexpected error: {e}; retry in {POLL_INTERVAL}s")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
