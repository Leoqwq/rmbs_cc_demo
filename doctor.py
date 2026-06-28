"""Read-only preflight checks (spec §9 + prerequisites). Never mutates state. Prints a
pass/fail report and exits nonzero if any check fails, so `make sync` can chain it.

  python doctor.py               # full: tooling + config + runtime reachability (after 'make up')
  python doctor.py --config-only # tooling + config only (skip TEE/decryption-node reachability;
                                 #   used by 'make sync', which runs BEFORE tunnels/nodes exist)
"""
import argparse
import importlib
import os
import shutil
import sys
import time

import requests
from dotenv import load_dotenv

REQUIRED_ENV = ["CHAIN_ID", "CONTRACT_ADDRESS", "TEE_URL", "TEE_ADDRESS",
                "ORACLE_ADDRESSES", "ORACLE_KEYS", "THRESHOLD", "DECRYPTION_NODE_URLS"]

# Representative third-party deps the demo needs at runtime — if `pip install -r
# requirements.txt` half-failed, at least one of these won't import.
CORE_DEPS = ["web3", "umbral", "eth_account", "requests", "dotenv", "fastapi", "uvicorn"]


def check(name, ok, detail=""):
    return {"name": name, "ok": bool(ok), "detail": detail}


def check_env_keys(env, required=REQUIRED_ENV):
    missing = [k for k in required if not env.get(k, "").strip()]
    return check(".env keys", not missing,
                 "all present" if not missing else f"missing/empty: {', '.join(missing)}")


def check_rpc_configured(env):
    ok = bool(env.get("RPC_URLS", "").strip() or env.get("RPC_URL", "").strip())
    return check("RPC endpoint", ok,
                 "RPC_URLS or RPC_URL set" if ok else "set RPC_URLS or RPC_URL in .env")


def check_tool(name, exe):
    path = shutil.which(exe)
    return check(name, path is not None, path or f"{exe} not found on PATH")


def check_python_deps(modules=CORE_DEPS, import_fn=importlib.import_module):
    missing = [m for m in modules if not _importable(m, import_fn)]
    return check("python deps", not missing,
                 "all importable" if not missing
                 else f"missing: {', '.join(missing)} — run: pip install -r requirements.txt")


def _importable(module, import_fn):
    try:
        import_fn(module)
        return True
    except Exception:  # noqa: BLE001 - any import failure means the dep isn't usable
        return False


def check_url(name, url, get=requests.get):
    try:
        r = get(url, timeout=5)
        return check(name, r.status_code == 200, f"{url} -> {r.status_code}")
    except Exception as e:  # noqa: BLE001
        return check(name, False, f"{url} unreachable: {e}")


def check_chain(name, rpc_url, post=requests.post, delay=2.0):
    """Confirm the chain is actually PRODUCING blocks (number advances), not just answering
    RPC — a sub-quorum QBFT chain (<3 of 4 validators) replies but never advances."""
    def _block():
        try:
            r = post(rpc_url, json={"jsonrpc": "2.0", "method": "eth_blockNumber",
                                    "params": [], "id": 1}, timeout=5)
            return int(r.json()["result"], 16)
        except Exception:  # noqa: BLE001
            return None

    b0 = _block()
    if b0 is None:
        return check(name, False, f"{rpc_url} unreachable (tunnel open? 'make infra-up' done?)")
    time.sleep(delay)
    b1 = _block()
    if b1 is not None and b1 > b0:
        return check(name, True, f"producing blocks ({b0} -> {b1})")
    return check(name, False,
                 f"reachable at block {b0} but NOT advancing — need >=3 of 4 validators online")


def format_report(results):
    lines = [f"[{'OK ' if r['ok'] else 'FAIL'}] {r['name']}: {r['detail']}" for r in results]
    passed = sum(1 for r in results if r["ok"])
    suffix = "" if passed == len(results) else f" — {len(results) - passed} FAILED"
    return "\n".join(lines + ["", f"{passed}/{len(results)} checks passed{suffix}"])


def run_all(env, runtime=True):
    """Tooling + config checks always; runtime reachability (TEE, decryption nodes) only
    when runtime=True. 'make sync' passes runtime=False — it runs before 'make up' opens
    the tunnels and starts the local nodes, so those checks would always (falsely) FAIL."""
    results = [check_tool("gcloud", "gcloud"), check_python_deps(),
               check_env_keys(env), check_rpc_configured(env)]
    if not runtime:
        return results
    rpc = (env.get("RPC_URL", "").strip()
           or next((u.strip() for u in env.get("RPC_URLS", "").split(",") if u.strip()), ""))
    if rpc:
        results.append(check_chain("chain", rpc))
    tee = env.get("TEE_URL", "").rstrip("/")
    if tee:
        results.append(check_url("TEE service", tee + "/tee_address"))
    for url in [u.strip() for u in env.get("DECRYPTION_NODE_URLS", "").split(",") if u.strip()]:
        # /docs is GET-accessible on any live FastAPI instance; /reencrypt is POST-only
        results.append(check_url(f"decryption node {url}", url.rstrip("/") + "/docs"))
    return results


def main(argv=None):
    p = argparse.ArgumentParser(description="Read-only preflight checks.")
    p.add_argument("--config-only", action="store_true",
                   help="skip TEE/decryption-node reachability (for use before 'make up')")
    args = p.parse_args(argv)
    load_dotenv()
    results = run_all(dict(os.environ), runtime=not args.config_only)
    print(format_report(results))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
