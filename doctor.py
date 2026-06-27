"""Read-only preflight checks (spec §9 + prerequisites). Never mutates state. Prints a
pass/fail report and exits nonzero if any check fails, so `make sync` can chain it.
"""
import os
import shutil
import sys

import requests
from dotenv import load_dotenv

REQUIRED_ENV = ["CHAIN_ID", "CONTRACT_ADDRESS", "TEE_URL", "TEE_ADDRESS",
                "ORACLE_ADDRESSES", "ORACLE_KEYS", "THRESHOLD", "DECRYPTION_NODE_URLS"]


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


def check_url(name, url, get=requests.get):
    try:
        r = get(url, timeout=5)
        return check(name, r.status_code == 200, f"{url} -> {r.status_code}")
    except Exception as e:  # noqa: BLE001
        return check(name, False, f"{url} unreachable: {e}")


def format_report(results):
    lines = [f"[{'OK ' if r['ok'] else 'FAIL'}] {r['name']}: {r['detail']}" for r in results]
    passed = sum(1 for r in results if r["ok"])
    suffix = "" if passed == len(results) else f" — {len(results) - passed} FAILED"
    return "\n".join(lines + ["", f"{passed}/{len(results)} checks passed{suffix}"])


def run_all(env):
    results = [check_tool("gcloud", "gcloud"), check_env_keys(env), check_rpc_configured(env)]
    tee = env.get("TEE_URL", "").rstrip("/")
    if tee:
        results.append(check_url("TEE service", tee + "/tee_address"))
    for url in [u.strip() for u in env.get("DECRYPTION_NODE_URLS", "").split(",") if u.strip()]:
        # /docs is GET-accessible on any live FastAPI instance; /reencrypt is POST-only
        results.append(check_url(f"decryption node {url}", url.rstrip("/") + "/docs"))
    return results


def main():
    load_dotenv()
    results = run_all(dict(os.environ))
    print(format_report(results))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
