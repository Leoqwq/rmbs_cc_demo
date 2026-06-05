"""Orchestrator (Oracle role): watch ComputeRequested, call the TEE, post results.

Plaintext pipeline — no decryption. For each new on-chain request it forwards
{dealId, period, iaf, paf} to the TEE, receives the result + TEE signature, and
sends postResult() back to the contract (signed by the funded deployer account).
"""
import json
import os
import time

import requests
from dotenv import load_dotenv
from web3 import Web3

load_dotenv()

RPC_URL = os.environ["RPC_URL"]
CHAIN_ID = int(os.environ["CHAIN_ID"])
CONTRACT_ADDRESS = Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"])
TEE_URL = os.environ["TEE_URL"].rstrip("/")
ADMIN_PK = os.environ["DEPLOYER_PRIVATE_KEY"]
POLL_INTERVAL = 3

ABI_PATH = os.path.join(
    os.path.dirname(__file__), "out", "ConfidentialCompute.sol", "ConfidentialCompute.json"
)


def load_abi():
    with open(ABI_PATH) as f:
        return json.load(f)["abi"]


def post_result(w3, contract, admin, request_id, body):
    result_hash = bytes.fromhex(body["resultHash"])
    result_json = json.dumps(body["result"], sort_keys=True, separators=(",", ":"))
    sig = bytes.fromhex(body["signature"][2:])

    tx = contract.functions.postResult(request_id, result_hash, result_json, sig).build_transaction(
        {
            "from": admin.address,
            "nonce": w3.eth.get_transaction_count(admin.address),
            "gas": 800000,
            "gasPrice": 0,
            "chainId": CHAIN_ID,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, ADMIN_PK)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"  postResult tx {tx_hash.hex()} status={receipt['status']}")


def handle_event(w3, contract, admin, event):
    request_id = event["args"]["id"]
    args = event["args"]
    print(f"\n>>> ComputeRequested id={request_id} deal={args['dealId']} "
          f"period={args['period']} IAF={args['iaf']} PAF={args['paf']}")

    print("  forwarding to TEE...")
    resp = requests.post(
        f"{TEE_URL}/compute",
        json={
            "dealId": args["dealId"],
            "period": args["period"],
            "iaf": args["iaf"],
            "paf": args["paf"],
        },
        timeout=30,
    )
    body = resp.json()
    if not body.get("success"):
        print(f"  TEE error: {body.get('error')}")
        return
    print(f"  TEE result: {body['result']}")
    print("  posting signed result on-chain...")
    post_result(w3, contract, admin, request_id, body)


def main():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    assert w3.is_connected(), f"Cannot connect to {RPC_URL}"
    admin = w3.eth.account.from_key(ADMIN_PK)
    contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=load_abi())

    print(f"Orchestrator up. chain_id={w3.eth.chain_id} contract={CONTRACT_ADDRESS}")
    print(f"Admin={admin.address}  TEE={TEE_URL}")

    last_block = w3.eth.block_number
    processed = set()
    print(f"Listening for ComputeRequested from block {last_block}...")
    while True:
        current = w3.eth.block_number
        if current > last_block:
            flt = contract.events.ComputeRequested.create_filter(
                from_block=last_block + 1, to_block=current
            )
            for event in flt.get_all_entries():
                key = (event["transactionHash"].hex(), event["logIndex"])
                if key not in processed:
                    handle_event(w3, contract, admin, event)
                    processed.add(key)
            last_block = current
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
