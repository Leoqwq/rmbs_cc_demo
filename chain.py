"""Resilient chain access: multi-RPC failover for the Besu validators.

Each validator exposes RPC on :8545, reached locally via one IAP tunnel per
validator (on distinct local ports). Configure ``RPC_URLS`` as a comma-separated
list of those tunnel endpoints so a single validator (or its tunnel) going
offline is transparent. ``RPC_URL`` (singular) is still honored as a fallback.

- ``connect_web3`` is for one-shot CLIs: connect to the first reachable endpoint.
- ``ResilientChain`` is for the long-running oracle agents: it keeps a contract
  bound to the active endpoint and, on a transport error, fails over to the next
  endpoint and retries. Contract reverts are NOT retried (they re-raise).
"""
import os
from typing import Any, Callable, List

import requests
from web3 import Web3
from web3.exceptions import TimeExhausted

# Transport-level failures that should trigger failover. A contract revert is
# NOT in here (web3 raises ContractLogicError / returns status 0), so genuine
# business failures are never silently retried against another node.
TRANSPORT_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.HTTPError,
    ConnectionError,
    TimeExhausted,
)


def get_rpc_urls() -> List[str]:
    """Ordered RPC endpoints. ``RPC_URLS`` (comma-separated) wins; else ``RPC_URL``."""
    multi = os.getenv("RPC_URLS")
    if multi:
        urls = [u.strip() for u in multi.split(",") if u.strip()]
        if urls:
            return urls
    single = os.getenv("RPC_URL")
    if single and single.strip():
        return [single.strip()]
    raise RuntimeError("Set RPC_URLS (comma-separated) or RPC_URL in the environment")


def _make_w3(url: str) -> Web3:
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))


def connect_web3(rpc_urls: List[str]) -> Web3:
    """Return a Web3 bound to the first reachable endpoint (for one-shot CLIs)."""
    for url in rpc_urls:
        w3 = _make_w3(url)
        if w3.is_connected():
            return w3
    raise ConnectionError(f"No reachable RPC endpoint among: {', '.join(rpc_urls)}")


class ResilientChain:
    """Holds ordered RPC endpoints and a contract bound to the active one.

    ``run(fn)`` executes ``fn(w3, contract)``; on a transport error it fails over
    to the next endpoint and retries, so one node/tunnel outage is transparent.
    """

    def __init__(self, rpc_urls: List[str], contract_address: str, abi: list):
        self.rpc_urls = rpc_urls
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.abi = abi
        self.idx = 0
        self.w3: Web3 = None  # type: ignore[assignment]
        self.url: str = ""
        self.contract = None
        self._connect_from(0)

    def _connect_from(self, start: int) -> None:
        n = len(self.rpc_urls)
        for offset in range(n):
            i = (start + offset) % n
            w3 = _make_w3(self.rpc_urls[i])
            if w3.is_connected():
                self.idx = i
                self.w3 = w3
                self.url = self.rpc_urls[i]
                self.contract = w3.eth.contract(address=self.contract_address, abi=self.abi)
                return
        raise ConnectionError(f"No reachable RPC endpoint among: {', '.join(self.rpc_urls)}")

    def failover(self) -> None:
        self._connect_from(self.idx + 1)
        print(f"  [failover] switched RPC -> {self.url}")

    def run(self, fn: Callable[[Web3, Any], Any]) -> Any:
        last: Exception | None = None
        for _ in range(len(self.rpc_urls)):
            try:
                return fn(self.w3, self.contract)
            except TRANSPORT_ERRORS as e:
                last = e
                self.failover()
        raise last  # type: ignore[misc]
