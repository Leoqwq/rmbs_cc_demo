"""User CLI: submit one period of cashflows to the ConfidentialCompute contract.

Usage:
  python submit_request.py --iaf 500000 --paf 1000000 [--deal TEST_SEQ_2024] [--period 1]
"""
import argparse
import json
import os

from dotenv import load_dotenv
from web3 import Web3

from chain import connect_web3, get_rpc_urls

load_dotenv()

CHAIN_ID = int(os.environ["CHAIN_ID"])
CONTRACT_ADDRESS = Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"])
PK = os.environ["DEPLOYER_PRIVATE_KEY"]

ABI_PATH = os.path.join(
    os.path.dirname(__file__), "out", "ConfidentialCompute.sol", "ConfidentialCompute.json"
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--iaf", type=int, required=True, help="Interest Available Funds")
    p.add_argument("--paf", type=int, required=True, help="Principal Available Funds")
    p.add_argument("--deal", default="TEST_SEQ_2024")
    p.add_argument("--period", type=int, default=1)
    args = p.parse_args()

    w3 = connect_web3(get_rpc_urls())
    acct = w3.eth.account.from_key(PK)
    with open(ABI_PATH) as f:
        abi = json.load(f)["abi"]
    contract = w3.eth.contract(address=CONTRACT_ADDRESS, abi=abi)

    tx = contract.functions.submitRequest(args.deal, args.period, args.iaf, args.paf).build_transaction(
        {
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "gas": 400000,
            "gasPrice": 0,
            "chainId": CHAIN_ID,
        }
    )
    signed = w3.eth.account.sign_transaction(tx, PK)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"submitRequest tx: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    logs = contract.events.ComputeRequested().process_receipt(receipt)
    request_id = logs[0]["args"]["id"]
    print(f"Request submitted: id={request_id} (deal={args.deal}, IAF={args.iaf}, PAF={args.paf})")
    print(f"Watch the orchestrator; then read the result with:")
    print(f"  python read_result.py {request_id}")


if __name__ == "__main__":
    main()
