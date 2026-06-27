"""Idempotency probes for bootstrap (spec §7). Pure functions are unit-tested; the CLI
wrappers read .env / the chain / the TEE and map results to exit codes for ops/bootstrap.sh:
exit 0 = already satisfied (skip the mutating step), 1 = action needed.
"""
import json
import os
import sys

import requests
from dotenv import load_dotenv
from web3 import Web3

import umbral_io as uio
from chain import connect_web3, get_rpc_urls


def contract_provisioned(contract, expected_oracle_count, expected_tee, expected_threshold):
    try:
        if contract.functions.oracleCount().call() != expected_oracle_count:
            return False
        if contract.functions.threshold().call() != expected_threshold:
            return False
        on_tee = contract.functions.teeAddress().call()
        return on_tee.lower() == expected_tee.lower()
    except Exception:  # noqa: BLE001 - any read failure means "not safely provisioned"
        return False


def under_funded(w3, oracle_addresses, floor_wei):
    out = []
    for a in oracle_addresses:
        addr = w3.to_checksum_address(a)
        if w3.eth.get_balance(addr) < floor_wei:
            out.append(addr)
    return out


def umbral_matches_enclave(state_path, enclave_pubkey_b64):
    if not os.path.exists(state_path):
        return False
    with open(state_path) as f:
        state = json.load(f)
    return state.get("enclave_public_key") == enclave_pubkey_b64


def oracle_keys_present(env):
    addrs = [a for a in env.get("ORACLE_ADDRESSES", "").split(",") if a.strip()]
    keys = [k for k in env.get("ORACLE_KEYS", "").split(",") if k.strip()]
    return bool(addrs) and len(addrs) == len(keys)


def _load_contract(env):
    abi_path = os.path.join(os.path.dirname(__file__), "out",
                            "ConfidentialCompute.sol", "ConfidentialCompute.json")
    with open(abi_path) as f:
        abi = json.load(f)["abi"]
    w3 = connect_web3(get_rpc_urls())
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(env["CONTRACT_ADDRESS"]), abi=abi)
    return w3, contract


def main(argv=None):
    load_dotenv()
    env = dict(os.environ)
    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else ""

    if cmd == "oracle-keys":
        return 0 if oracle_keys_present(env) else 1
    if cmd == "contract":
        if not env.get("CONTRACT_ADDRESS"):
            return 1
        _, contract = _load_contract(env)
        n = len([a for a in env["ORACLE_ADDRESSES"].split(",") if a.strip()])
        ok = contract_provisioned(contract, n, env["TEE_ADDRESS"], int(env["THRESHOLD"]))
        return 0 if ok else 1
    if cmd == "umbral":
        r = requests.get(env["TEE_URL"].rstrip("/") + "/enclave_pubkey", timeout=10)
        r.raise_for_status()
        return 0 if umbral_matches_enclave(uio.DEFAULT_STATE, r.json()["pubkey"]) else 1
    if cmd == "funded":
        w3 = connect_web3(get_rpc_urls())
        floor = w3.to_wei(float(env.get("ORACLE_FUND_ETHER", "1")), "ether")
        addrs = [a for a in env["ORACLE_ADDRESSES"].split(",") if a.strip()]
        under = under_funded(w3, addrs, floor)
        for a in under:
            print(a)
        return 0 if not under else 1
    print(f"unknown check: {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
