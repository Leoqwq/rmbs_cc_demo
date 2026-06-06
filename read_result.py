"""Read a stored compute result from the contract: python read_result.py <id>."""
import json
import os
import sys

from dotenv import load_dotenv
from web3 import Web3

from chain import connect_web3, get_rpc_urls

load_dotenv()

w3 = connect_web3(get_rpc_urls())
with open(os.path.join(os.path.dirname(__file__), "out",
                       "ConfidentialCompute.sol", "ConfidentialCompute.json")) as f:
    abi = json.load(f)["abi"]
contract = w3.eth.contract(
    address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]),
    abi=abi,
)

request_id = int(sys.argv[1])
posted, result_hash, result_json = contract.functions.getResult(request_id).call()
print(f"posted={posted}")
print(f"resultHash=0x{result_hash.hex()}")
print(f"resultJson={result_json}")
if posted:
    print("parsed:", json.dumps(json.loads(result_json), indent=2))
