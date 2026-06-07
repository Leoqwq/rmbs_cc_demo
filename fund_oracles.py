"""Top up each oracle account with native gas from the deployer.

Each oracle agent sends its own attest() tx, so each oracle account needs a little
native balance. Usage: python fund_oracles.py 0xOracle1 0xOracle2 ...
(or it reads ORACLE_ADDRESSES from .env if no args).
"""
import os
import sys

from dotenv import load_dotenv
from web3 import Web3

from chain import connect_web3, get_rpc_urls

load_dotenv()
CHAIN_ID = int(os.environ["CHAIN_ID"])
PK = os.environ["DEPLOYER_PRIVATE_KEY"]
AMOUNT_ETHER = float(os.getenv("ORACLE_FUND_ETHER", "1"))


def main():
    addrs = sys.argv[1:] or [a.strip() for a in os.environ["ORACLE_ADDRESSES"].split(",") if a.strip()]
    w3 = connect_web3(get_rpc_urls())
    acct = w3.eth.account.from_key(PK)
    nonce = w3.eth.get_transaction_count(acct.address)
    value = w3.to_wei(AMOUNT_ETHER, "ether")
    for addr in addrs:
        to = Web3.to_checksum_address(addr)
        tx = {
            "from": acct.address, "to": to, "value": value,
            "nonce": nonce, "gas": 21000, "gasPrice": w3.eth.gas_price, "chainId": CHAIN_ID,
        }
        signed = w3.eth.account.sign_transaction(tx, PK)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(h)
        print(f"funded {to} with {AMOUNT_ETHER} (tx {h.hex()})")
        nonce += 1


if __name__ == "__main__":
    main()
