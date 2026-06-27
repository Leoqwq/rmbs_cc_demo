# Startup Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ~10-terminal manual startup in `RUNBOOK.md` with a small `make` surface, where one-time/on-demand owner scripts are idempotent and never clobber the already-provisioned `.env`/contract, and teammates join via `sync` (not `bootstrap`).

**Architecture:** Root `Makefile` → thin bash glue in `ops/` (gcloud, tunnels, tmux, scp, forge) → unit-tested Python helpers (`config_env.py`, `provision_checks.py`, `doctor.py`, `run_oracle_agents.py`) for the stateful, must-be-correct logic. Reuses existing `chain.py` / `umbral_io.py`. Background processes + tunnels tracked in a gitignored `.run/`.

**Tech Stack:** Python 3 (web3, requests, python-dotenv, pyUmbral — already in `.venv`), Bash, GNU Make, Foundry (owner only), `gcloud`.

**Spec:** `docs/superpowers/specs/2026-06-26-startup-automation-design.md` — read §2 (safety property) and §7 (idempotency guards) before starting.

---

## File Structure

**New (Python, unit-tested):** `config_env.py`, `provision_checks.py`, `doctor.py`, `run_oracle_agents.py` (+ tests in `tests/`).
**New (bash glue):** `ops/lib.sh`, `ops/up.sh`, `ops/down.sh`, `ops/status.sh`, `ops/infra_up.sh`, `ops/infra_down.sh`, `ops/bootstrap.sh`, `ops/publish_config.sh`, `ops/sync.sh`.
**New (root):** `Makefile`.
**Modified:** `run_decryption_nodes.py` (SIGTERM cleanup), `.gitignore`, `.env.example`, `README.md`, `docs/FUTURE_WORK.md`.

Run all Python steps from repo root with the venv active: `source .venv/bin/activate`.

---

## Task 1: Repo hygiene — gitignore, env example, future-work note

**Files:**
- Modify: `.gitignore`
- Modify: `.env.example`
- Modify: `docs/FUTURE_WORK.md`

- [ ] **Step 1: Add runtime artifacts to `.gitignore`**

Append these lines to `.gitignore`:

```
.run/
.env.bak.*
```

- [ ] **Step 2: Add `ORACLE_KEYS` + owner/teammate notes to `.env.example`**

In `.env.example`, replace the per-agent block (the `ORACLE_ID` / `ORACLE_KEY` lines) with a comma-separated `ORACLE_KEYS` list the launcher enumerates, and add the umbral params bootstrap reads. Add after the `THRESHOLD=3` / `ORACLE_FUND_ETHER=1` lines:

```
# All oracle private keys, comma-separated, parallel to ORACLE_ADDRESSES.
# run_oracle_agents.py enumerates these (ORACLE_ID = position, starting at 1).
ORACLE_KEYS=0xkey1,0xkey2,0xkey3,0xkey4

# Umbral keygen parameters (owner bootstrap): shares = number of decryption nodes,
# threshold = re-encryption quorum m (NOT the oracle attestation THRESHOLD above).
UMBRAL_SHARES=3
UMBRAL_THRESHOLD=2
# Decryption-node base port (macOS AirPlay occupies 5000; use 5005).
DEC_BASE_PORT=5005
```

- [ ] **Step 3: Record the shared-deployer-key simplification in `docs/FUTURE_WORK.md`**

Append a new numbered item (use the next free number) titled "Shared genesis/deployer key in the member config bundle", noting: `make sync` distributes `members.env` containing the shared `DEPLOYER_PRIVATE_KEY` (needed to pay for `submitRequest`) and the 4 `ORACLE_KEYS`; a real deployment would issue per-member funded accounts and use a secret manager instead of sharing the genesis key. Reference the automation spec §6.

- [ ] **Step 4: Commit**

```bash
git add .gitignore .env.example docs/FUTURE_WORK.md
git commit -m "chore: env+gitignore+future-work prep for startup automation"
```

---

## Task 2: `config_env.py` — safe .env editing (the anti-clobber core)

**Files:**
- Create: `config_env.py`
- Test: `tests/test_config_env.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config_env.py
import os
import config_env as ce


def write(path, text):
    path.write_text(text)
    return str(path)


def test_parse_env_last_wins_and_ignores_comments(tmp_path):
    p = write(tmp_path / ".env", "# c\nA=1\nB=2\nA=3\n")
    assert ce.parse_env(p) == {"A": "3", "B": "2"}


def test_set_absent_key_appends(tmp_path):
    p = write(tmp_path / ".env", "A=1\n")
    res = ce.set_keys(p, {"B": "2"})
    assert res["changed"] == ["B"] and res["skipped"] == []
    assert ce.parse_env(p) == {"A": "1", "B": "2"}


def test_set_present_key_skipped_without_force(tmp_path):
    p = write(tmp_path / ".env", "A=1\n")
    res = ce.set_keys(p, {"A": "9"})
    assert res["changed"] == [] and res["skipped"] == ["A"]
    assert ce.parse_env(p)["A"] == "1"  # unchanged


def test_set_present_key_replaced_with_force(tmp_path):
    p = write(tmp_path / ".env", "A=1\nB=2\n")
    res = ce.set_keys(p, {"A": "9"}, force=True)
    assert res["changed"] == ["A"]
    assert ce.parse_env(p) == {"A": "9", "B": "2"}  # B preserved


def test_no_change_writes_no_backup(tmp_path):
    p = write(tmp_path / ".env", "A=1\n")
    res = ce.set_keys(p, {"A": "9"})  # skipped
    assert res["backup"] is None
    assert not list(tmp_path.glob(".env.bak.*"))


def test_change_writes_backup_and_preserves_other_keys(tmp_path):
    p = write(tmp_path / ".env", "A=1\nB=2\n")
    res = ce.set_keys(p, {"C": "3"})
    assert res["backup"] is not None and os.path.exists(res["backup"])
    assert ce.parse_env(p) == {"A": "1", "B": "2", "C": "3"}


def test_merge_file_pulls_keys(tmp_path):
    src = write(tmp_path / "members.env", "X=10\nY=20\n")
    dst = write(tmp_path / ".env", "X=1\n")
    res = ce.merge_file(src, dst, force=True)
    assert ce.parse_env(dst) == {"X": "10", "Y": "20"}
    assert set(res["changed"]) == {"X", "Y"}


def test_merge_into_missing_target_creates_it(tmp_path):
    src = write(tmp_path / "members.env", "X=10\n")
    dst = str(tmp_path / ".env")  # does not exist
    ce.merge_file(src, dst)
    assert ce.parse_env(dst) == {"X": "10"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config_env.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'config_env'`.

- [ ] **Step 3: Implement `config_env.py`**

```python
"""Safe .env editing for the automation scripts.

Central guarantee (spec §2): writes never drop or silently overwrite existing keys.
- set_keys(..., force=False) appends a key only if absent; with force=True it replaces
  the last occurrence in place. Either way it backs up first and replaces atomically.
- merge_file pulls KEY=VALUE pairs from a source file into the target via set_keys.

CLI:
  python config_env.py set   --into .env KEY=VALUE [KEY2=V2 ...] [--force]
  python config_env.py merge --from members.env --into .env [--force]
"""
import argparse
import os
import re
import shutil
import sys
import time

_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return f.read().splitlines()


def parse_env(path):
    """Return {key: value} with last-wins semantics (matches python-dotenv)."""
    result = {}
    for line in _read_lines(path):
        if line.strip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def _key_line_indexes(lines, key):
    idxs = []
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m and m.group(1) == key:
            idxs.append(i)
    return idxs


def _atomic_write(path, lines):
    backup = None
    if os.path.exists(path):
        backup = f"{path}.bak.{int(time.time())}"
        shutil.copy2(path, backup)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return backup


def set_keys(path, updates, force=False):
    """Apply {key: value} to the .env at `path`.

    Returns {"changed": [...], "skipped": [...], "backup": path|None}. A key already
    present is skipped unless force=True (then its last occurrence is replaced). Backs up
    and replaces atomically only when something actually changes.
    """
    lines = _read_lines(path)
    changed, skipped = [], []
    for key, value in updates.items():
        idxs = _key_line_indexes(lines, key)
        if idxs and not force:
            skipped.append(key)
        elif idxs and force:
            lines[idxs[-1]] = f"{key}={value}"
            changed.append(key)
        else:
            lines.append(f"{key}={value}")
            changed.append(key)
    backup = _atomic_write(path, lines) if changed else None
    return {"changed": changed, "skipped": skipped, "backup": backup}


def merge_file(src, dst, force=False):
    return set_keys(dst, parse_env(src), force=force)


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("--into", required=True)
    s.add_argument("--force", action="store_true")
    s.add_argument("pairs", nargs="+", help="KEY=VALUE")
    m = sub.add_parser("merge")
    m.add_argument("--from", dest="src", required=True)
    m.add_argument("--into", required=True)
    m.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "set":
        updates = {}
        for pair in a.pairs:
            k, _, v = pair.partition("=")
            updates[k] = v
        res = set_keys(a.into, updates, force=a.force)
    else:
        res = merge_file(a.src, a.into, force=a.force)
    print(f"changed={res['changed']} skipped={res['skipped']} backup={res['backup']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config_env.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add config_env.py tests/test_config_env.py
git commit -m "feat: config_env.py — non-clobbering .env editing with backups"
```

---

## Task 3: `provision_checks.py` — idempotency probes

**Files:**
- Create: `provision_checks.py`
- Test: `tests/test_provision_checks.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_provision_checks.py
import json
import provision_checks as pc


class _Fn:
    def __init__(self, value):
        self._value = value

    def call(self):
        return self._value


class _Contract:
    """Minimal stand-in for a web3 contract: .functions.NAME().call()."""
    def __init__(self, oracle_count, threshold, tee):
        self._v = {"oracleCount": oracle_count, "threshold": threshold, "teeAddress": tee}

    class _Functions:
        def __init__(self, outer):
            self._outer = outer

        def __getattr__(self, name):
            return lambda: _Fn(self._outer._v[name])

    @property
    def functions(self):
        return _Contract._Functions(self)


def test_contract_provisioned_true_when_all_match():
    c = _Contract(4, 3, "0xAbC0000000000000000000000000000000000001")
    assert pc.contract_provisioned(c, 4, "0xabc0000000000000000000000000000000000001", 3) is True


def test_contract_provisioned_false_on_oracle_count_mismatch():
    c = _Contract(3, 3, "0xAbC0000000000000000000000000000000000001")
    assert pc.contract_provisioned(c, 4, "0xAbC0000000000000000000000000000000000001", 3) is False


def test_contract_provisioned_false_on_tee_mismatch():
    c = _Contract(4, 3, "0xdeadbeef00000000000000000000000000000000")
    assert pc.contract_provisioned(c, 4, "0xabc0000000000000000000000000000000000001", 3) is False


def test_under_funded_lists_only_below_floor():
    class _Eth:
        def get_balance(self, addr):
            return {"0x" + "1" * 40: 10, "0x" + "2" * 40: 0}[addr.lower()]

    class _W3:
        eth = _Eth()

        @staticmethod
        def to_checksum_address(a):
            return a  # identity is fine for the fake

    under = pc.under_funded(_W3(), ["0x" + "1" * 40, "0x" + "2" * 40], floor_wei=5)
    assert under == ["0x" + "2" * 40]


def test_umbral_matches_enclave(tmp_path):
    p = tmp_path / "umbral_state.json"
    p.write_text(json.dumps({"enclave_public_key": "PUBKEY_B64"}))
    assert pc.umbral_matches_enclave(str(p), "PUBKEY_B64") is True
    assert pc.umbral_matches_enclave(str(p), "OTHER") is False
    assert pc.umbral_matches_enclave(str(tmp_path / "missing.json"), "PUBKEY_B64") is False


def test_oracle_keys_present():
    assert pc.oracle_keys_present({"ORACLE_ADDRESSES": "0xa,0xb", "ORACLE_KEYS": "0x1,0x2"}) is True
    assert pc.oracle_keys_present({"ORACLE_ADDRESSES": "0xa,0xb", "ORACLE_KEYS": "0x1"}) is False
    assert pc.oracle_keys_present({"ORACLE_ADDRESSES": "", "ORACLE_KEYS": ""}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_provision_checks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'provision_checks'`.

- [ ] **Step 3: Implement `provision_checks.py`**

Note: `to_checksum_address` in `under_funded` is called via the passed `w3`, so the fake controls it (the test's `_W3` returns identity). The real CLI path uses web3's checksumming.

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_provision_checks.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add provision_checks.py tests/test_provision_checks.py
git commit -m "feat: provision_checks.py — idempotency probes for bootstrap"
```

---

## Task 4: `run_oracle_agents.py` + SIGTERM cleanup in `run_decryption_nodes.py`

**Files:**
- Create: `run_oracle_agents.py`
- Modify: `run_decryption_nodes.py`
- Test: `tests/test_run_oracle_agents.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_oracle_agents.py
import pytest
import run_oracle_agents as roa


def test_parse_oracle_keys_splits_and_strips():
    assert roa.parse_oracle_keys({"ORACLE_KEYS": "0x1, 0x2 ,0x3"}) == ["0x1", "0x2", "0x3"]


def test_parse_oracle_keys_empty_raises():
    with pytest.raises(SystemExit):
        roa.parse_oracle_keys({"ORACLE_KEYS": "  "})


def test_build_commands_assigns_ids_and_keys():
    cmds = roa.build_commands(["0xa", "0xb"], python="python3", script="oracle_agent.py")
    assert len(cmds) == 2
    assert cmds[0][0] == {"ORACLE_ID": "1", "ORACLE_KEY": "0xa"}
    assert cmds[1][0] == {"ORACLE_ID": "2", "ORACLE_KEY": "0xb"}
    assert cmds[0][1] == ["python3", "oracle_agent.py"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_run_oracle_agents.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'run_oracle_agents'`.

- [ ] **Step 3: Implement `run_oracle_agents.py`**

```python
"""Launch one oracle_agent.py per key in ORACLE_KEYS (mirrors run_decryption_nodes.py).

Collapses the runbook's N agent terminals into one background process group. Each child
gets ORACLE_ID=<position, from 1> and ORACLE_KEY=<key> in its environment; on SIGTERM or
Ctrl-C every child is terminated so `make down` cleans up.

  python run_oracle_agents.py            # one agent per ORACLE_KEYS entry
"""
import os
import signal
import subprocess
import sys

from dotenv import load_dotenv


def parse_oracle_keys(env):
    keys = [k.strip() for k in env.get("ORACLE_KEYS", "").split(",") if k.strip()]
    if not keys:
        raise SystemExit("ORACLE_KEYS is empty — set a comma-separated list in .env")
    return keys


def build_commands(keys, python=None, script="oracle_agent.py"):
    python = python or sys.executable
    return [({"ORACLE_ID": str(i), "ORACLE_KEY": key}, [python, script])
            for i, key in enumerate(keys, start=1)]


def main():
    load_dotenv()
    keys = parse_oracle_keys(os.environ)
    procs = []
    for overrides, cmd in build_commands(keys):
        env = os.environ.copy()
        env.update(overrides)
        print(f"Starting oracle agent {overrides['ORACLE_ID']}")
        procs.append(subprocess.Popen(cmd, env=env))

    def _terminate(*_):
        for p in procs:
            p.terminate()

    signal.signal(signal.SIGTERM, _terminate)
    print("PIDs:", [p.pid for p in procs], "— Ctrl+C / SIGTERM to stop")
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        _terminate()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_run_oracle_agents.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Add SIGTERM cleanup to `run_decryption_nodes.py`**

So `make down` (which sends SIGTERM to the launcher) also terminates the child uvicorns. Add `import signal` to the imports, and register a handler before the `try/except KeyboardInterrupt` block. Modify the tail of `main()`:

```python
    print("PIDs:", [p.pid for p in procs], "— Ctrl+C to stop")

    def _terminate(*_):
        for p in procs:
            p.terminate()

    signal.signal(signal.SIGTERM, _terminate)
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        _terminate()
```

(Add `import signal` at the top alongside the existing `import os` / `import subprocess`.)

- [ ] **Step 6: Verify the full Python suite still passes**

Run: `python -m pytest tests/ -q`
Expected: PASS (existing 29 + new tests).

- [ ] **Step 7: Commit**

```bash
git add run_oracle_agents.py run_decryption_nodes.py tests/test_run_oracle_agents.py
git commit -m "feat: run_oracle_agents.py launcher + SIGTERM cleanup for launchers"
```

---

## Task 5: `doctor.py` — read-only preflight

**Files:**
- Create: `doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doctor.py
import doctor


def test_check_env_keys_reports_missing():
    r = doctor.check_env_keys({"CHAIN_ID": "1"}, required=["CHAIN_ID", "TEE_URL"])
    assert r["ok"] is False and "TEE_URL" in r["detail"]


def test_check_env_keys_all_present():
    r = doctor.check_env_keys({"CHAIN_ID": "1", "TEE_URL": "x"}, required=["CHAIN_ID", "TEE_URL"])
    assert r["ok"] is True


def test_check_url_ok_with_injected_getter():
    class _Resp:
        status_code = 200

    r = doctor.check_url("TEE", "http://x/tee_address", get=lambda url, timeout: _Resp())
    assert r["ok"] is True and "200" in r["detail"]


def test_check_url_failure_is_caught():
    def _boom(url, timeout):
        raise OSError("refused")

    r = doctor.check_url("TEE", "http://x", get=_boom)
    assert r["ok"] is False and "unreachable" in r["detail"]


def test_format_report_counts_pass_fail():
    results = [doctor.check("a", True, "ok"), doctor.check("b", False, "nope")]
    out = doctor.format_report(results)
    assert "[OK ] a" in out and "[FAIL] b" in out and "1/2 checks passed" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_doctor.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'doctor'`.

- [ ] **Step 3: Implement `doctor.py`**

```python
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
    results = [check_tool("gcloud", "gcloud"), check_env_keys(env)]
    tee = env.get("TEE_URL", "").rstrip("/")
    if tee:
        results.append(check_url("TEE service", tee + "/tee_address"))
    for url in [u.strip() for u in env.get("DECRYPTION_NODE_URLS", "").split(",") if u.strip()]:
        results.append(check_url(f"decryption node {url}", url.rstrip("/") + "/docs"))
    return results


def main():
    load_dotenv()
    results = run_all(dict(os.environ))
    print(format_report(results))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_doctor.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add doctor.py tests/test_doctor.py
git commit -m "feat: doctor.py — read-only preflight checks"
```

---

## Task 6: `ops/lib.sh` — shared bash helpers

**Files:**
- Create: `ops/lib.sh`

- [ ] **Step 1: Write `ops/lib.sh`**

```bash
#!/usr/bin/env bash
# Shared helpers for the ops/ scripts: env loading, the instance/zone table, the .run/
# PID+log registry, and a bounded health poller. Source this at the top of each ops script.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
mkdir -p "$RUN_DIR"

# Instance -> zone layout (from RUNBOOK stage 0).
ZONE_A="us-central1-a"; ZONE_B="us-central1-b"; ZONE_C="us-central1-c"
INSTANCES_A="bootnode-a validator-1 validator-4 tee-node"
INSTANCES_B="bootnode-b validator-2"
INSTANCES_C="validator-3"

log()  { printf '\033[36m[ops]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[ops]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[ops] %s\033[0m\n' "$*" >&2; exit 1; }

load_env() {
  [ -f "$ROOT/.env" ] || die ".env not found — teammates run 'make sync' first"
  set -a; . "$ROOT/.env"; set +a
}

activate_venv() {
  [ -f "$ROOT/.venv/bin/activate" ] || die ".venv missing — create it (see RUNBOOK stage 1)"
  # shellcheck disable=SC1091
  . "$ROOT/.venv/bin/activate"
}

start_bg() {  # start_bg <name> <cmd...>  — run in background, record pid + log
  local name="$1"; shift
  "$@" >"$RUN_DIR/$name.log" 2>&1 &
  echo $! >"$RUN_DIR/$name.pid"
  log "started $name (pid $!) -> .run/$name.log"
}

stop_pidfile() {  # stop_pidfile <path-to-.pid>
  local f="$1" name pid
  [ -f "$f" ] || return 0
  name="$(basename "$f" .pid)"; pid="$(cat "$f")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    log "stopped $name (pid $pid)"
  fi
  rm -f "$f"
}

wait_for() {  # wait_for <desc> <max_secs> <cmd...>  — poll until cmd succeeds or timeout
  local desc="$1" max="$2"; shift 2
  local i=0
  until "$@" >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -ge "$max" ] && die "timeout waiting for $desc (${max}s) — check .run/*.log"
    sleep 1
  done
  log "ready: $desc"
}
```

- [ ] **Step 2: Verify it sources without error**

Run: `bash -c 'source ops/lib.sh && echo "lib ok: $ZONE_A / run=$RUN_DIR" && type wait_for >/dev/null && echo helpers-ok'`
Expected: prints `lib ok: us-central1-a / run=...` and `helpers-ok`, exit 0, and creates `.run/`.

- [ ] **Step 3: Commit**

```bash
git add ops/lib.sh
git commit -m "feat: ops/lib.sh — shared bash helpers (env, .run registry, wait_for)"
```

---

## Task 7: `ops/up.sh`, `ops/down.sh`, `ops/status.sh` — teammate runtime

**Files:**
- Create: `ops/up.sh`, `ops/down.sh`, `ops/status.sh`

- [ ] **Step 1: Write `ops/up.sh`**

```bash
#!/usr/bin/env bash
# Teammate runtime: open the two tunnels, gate on chain + TEE health, start the
# decryption nodes, gate on them, then start the oracle agents. Shared cloud infra is
# assumed already up (owner ran 'make infra-up'). Logs + pids land in .run/.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
activate_venv

warn "Run the demo ONE PERSON AT A TIME — teammates share oracle keys; concurrent agents collide on nonces."

# 1) Tunnels (chain RPC + TEE port-forward). 127.0.0.1 everywhere (RUNBOOK stage 4).
start_bg tunnel-chain gcloud compute start-iap-tunnel validator-1 8545 \
  --local-host-port=127.0.0.1:8545 --zone="$ZONE_A"
start_bg tunnel-tee gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  -- -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 8000:127.0.0.1:8000

# 2) Health gates.
wait_for "chain RPC (block number)" 90 \
  curl -sf -X POST -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
  http://127.0.0.1:8545
wait_for "TEE service" 90 curl -sf http://127.0.0.1:8000/tee_address

# 3) Decryption nodes (BASE_PORT avoids macOS AirPlay on 5000).
start_bg decnodes env BASE_PORT="${DEC_BASE_PORT:-5005}" python run_decryption_nodes.py
IFS=',' read -ra _NODES <<< "$DECRYPTION_NODE_URLS"
for url in "${_NODES[@]}"; do
  url="$(echo "$url" | xargs)"  # trim
  wait_for "decryption node $url" 30 curl -sf -o /dev/null "${url%/}/docs"
done

# 4) Oracle agents (one per ORACLE_KEYS entry).
start_bg oracles python run_oracle_agents.py

log "up complete. 'make status' to inspect · 'make demo' to run · 'make down' to stop."
```

- [ ] **Step 2: Write `ops/down.sh`**

```bash
#!/usr/bin/env bash
# Stop everything 'up' started (agents, decryption nodes, both tunnels). Leaves the
# shared cloud instances and the remote TEE untouched.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

for name in oracles decnodes tunnel-tee tunnel-chain; do
  stop_pidfile "$RUN_DIR/$name.pid"
done
log "down complete (shared instances + remote TEE untouched)."
```

- [ ] **Step 3: Write `ops/status.sh`**

```bash
#!/usr/bin/env bash
# Show tracked processes (.run/*.pid) and chain/TEE reachability.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

shopt -s nullglob
pids=("$RUN_DIR"/*.pid)
if [ ${#pids[@]} -eq 0 ]; then
  log "no tracked processes (.run empty)"
else
  for f in "${pids[@]}"; do
    name="$(basename "$f" .pid)"; pid="$(cat "$f")"
    if kill -0 "$pid" 2>/dev/null; then echo "RUNNING  $name (pid $pid)"
    else echo "DEAD     $name (stale pidfile)"; fi
  done
fi

curl -sf http://127.0.0.1:8000/tee_address >/dev/null 2>&1 \
  && echo "TEE      reachable" || echo "TEE      unreachable"
curl -sf -X POST -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
  http://127.0.0.1:8545 >/dev/null 2>&1 \
  && echo "chain    reachable" || echo "chain    unreachable"
```

- [ ] **Step 4: Syntax-check all three**

Run: `bash -n ops/up.sh && bash -n ops/down.sh && bash -n ops/status.sh && echo syntax-ok`
Expected: `syntax-ok`.

- [ ] **Step 5: Commit**

```bash
git add ops/up.sh ops/down.sh ops/status.sh
git commit -m "feat: ops up/down/status — teammate runtime lifecycle"
```

---

## Task 8: `ops/infra_up.sh`, `ops/infra_down.sh` — owner shared infra

**Files:**
- Create: `ops/infra_up.sh`, `ops/infra_down.sh`

- [ ] **Step 1: Write `ops/infra_up.sh`**

```bash
#!/usr/bin/env bash
# Owner: start the shared instances and ensure the TEE service is running on tee-node.
# Idempotent: if the TEE already answers, the remote step is a no-op.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "starting shared instances (QBFT needs >=3 validators online to produce blocks)..."
gcloud compute instances start $INSTANCES_A --zone="$ZONE_A"
gcloud compute instances start $INSTANCES_B --zone="$ZONE_B"
gcloud compute instances start $INSTANCES_C --zone="$ZONE_C"

log "ensuring the TEE service is up on tee-node (tmux session 'tee')..."
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap --command='
  set -e
  if curl -sf http://127.0.0.1:8000/tee_address >/dev/null 2>&1; then
    echo "TEE already running"; exit 0
  fi
  cd ~/rmbs_cc_demo
  tmux kill-session -t tee 2>/dev/null || true
  TERM=xterm-256color tmux new-session -d -s tee \
    "source .venv/bin/activate && python -m tee.tee_service"
  echo "TEE started in tmux session tee"
'
log "infra-up done. Open tunnels (make up, or just the tunnels) and confirm block production."
```

- [ ] **Step 2: Write `ops/infra_down.sh`**

```bash
#!/usr/bin/env bash
# Owner: stop the shared instances (cost control). Persistent disk state survives.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

gcloud compute instances stop $INSTANCES_A --zone="$ZONE_A"
gcloud compute instances stop $INSTANCES_B --zone="$ZONE_B"
gcloud compute instances stop $INSTANCES_C --zone="$ZONE_C"
log "infra-down done."
```

- [ ] **Step 3: Syntax-check**

Run: `bash -n ops/infra_up.sh && bash -n ops/infra_down.sh && echo syntax-ok`
Expected: `syntax-ok`.

- [ ] **Step 4: Commit**

```bash
git add ops/infra_up.sh ops/infra_down.sh
git commit -m "feat: ops infra up/down — owner shared instance + TEE lifecycle"
```

---

## Task 9: `ops/bootstrap.sh`, `ops/publish_config.sh`, `ops/sync.sh` — provisioning + config bundle

**Files:**
- Create: `ops/bootstrap.sh`, `ops/publish_config.sh`, `ops/sync.sh`

This is the task that must honor spec §2 (no-op on an already-provisioned repo). `bootstrap.sh` calls `provision_checks.py` before every mutating step and only acts when the probe says action is needed. Assumes chain + TEE are reachable (owner has run `infra-up` and opened tunnels).

- [ ] **Step 1: Write `ops/bootstrap.sh`**

```bash
#!/usr/bin/env bash
# Owner: idempotent ensure-provisioned. Each mutating step is guarded by a probe in
# provision_checks.py and SKIPS when already satisfied (spec §2/§7). On a fully
# provisioned repo this changes nothing on disk or chain.
# Prereq: 'make infra-up' done + chain/TEE tunnels open.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
activate_venv
FORGE="${FORGE:-$HOME/.foundry/bin/forge}"

wait_for "chain RPC" 30 \
  curl -sf -X POST -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' "$RPC_URL"
wait_for "TEE service" 30 curl -sf "${TEE_URL%/}/tee_address"

# 1) Oracle identities. We never auto-generate over an existing set (that would orphan the
#    deployed contract's registered oracles); if missing, stop with instructions.
if python provision_checks.py oracle-keys; then
  log "oracle keys present — skip generation"
else
  die "ORACLE_ADDRESSES/ORACLE_KEYS missing or unequal length. Generate n keys with
       'cast wallet new' (x4), then set ORACLE_ADDRESSES and ORACLE_KEYS in .env, and re-run."
fi

# 2) Contract. Deploy only if not already provisioned on-chain.
if python provision_checks.py contract; then
  log "contract already provisioned on-chain — skip deploy"
else
  log "deploying ConfidentialCompute..."
  ( cd "$ROOT" && "$FORGE" build >/dev/null )
  ( cd "$ROOT" && "$FORGE" script script/Deploy.s.sol:Deploy \
      --rpc-url "$RPC_URL" --broadcast --legacy ) | tee "$RUN_DIR/deploy.log"
  ADDR="$(grep -oE 'deployed at: 0x[0-9a-fA-F]{40}' "$RUN_DIR/deploy.log" \
          | grep -oE '0x[0-9a-fA-F]{40}' | tail -1)"
  [ -n "$ADDR" ] || die "could not parse deployed address from .run/deploy.log"
  # We just deployed, so force-write the new address (only reached when not provisioned).
  python config_env.py set --force --into "$ROOT/.env" "CONTRACT_ADDRESS=$ADDR"
  log "deployed at $ADDR (written to .env, backup made)"
  load_env
fi

# 3) Umbral keygen. Skip when kd/umbral_state.json already matches the live enclave key.
if python provision_checks.py umbral; then
  log "umbral state matches the live enclave key — skip keygen"
else
  log "running keygen (shares=${UMBRAL_SHARES:-3}, threshold=${UMBRAL_THRESHOLD:-2})..."
  python keygen.py --shares "${UMBRAL_SHARES:-3}" --threshold "${UMBRAL_THRESHOLD:-2}"
  warn "keygen produced new kfrags — run 'make publish-config' to push umbral_state.json to the TEE."
fi

# 4) Fund oracles. provision_checks prints under-funded addresses (and exits 1) when any.
UNDER="$(python provision_checks.py funded || true)"
if [ -z "$UNDER" ]; then
  log "all oracles funded — skip"
else
  log "funding under-funded oracles: $UNDER"
  # shellcheck disable=SC2086
  python fund_oracles.py $UNDER
fi

log "bootstrap complete."
```

- [ ] **Step 2: Write `ops/publish_config.sh`**

```bash
#!/usr/bin/env bash
# Owner: build the ABI and push the member bundle (members.env + umbral_state.json + ABI)
# to the shared tee-node so teammates can 'make sync'.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
load_env
FORGE="${FORGE:-$HOME/.foundry/bin/forge}"
ABI="$ROOT/out/ConfidentialCompute.sol/ConfidentialCompute.json"

log "building ABI for the member bundle..."
( cd "$ROOT" && "$FORGE" build >/dev/null )
[ -f "$ABI" ] || die "ABI not found at $ABI after forge build"

gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='mkdir -p ~/rmbs_cc_demo/share'
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/.env" tee-node:~/rmbs_cc_demo/share/members.env
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/kd/umbral_state.json" tee-node:~/rmbs_cc_demo/share/umbral_state.json
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ABI" tee-node:~/rmbs_cc_demo/share/ConfidentialCompute.json
log "published members.env + umbral_state.json + ABI to tee-node:~/rmbs_cc_demo/share/"
```

- [ ] **Step 3: Write `ops/sync.sh`**

```bash
#!/usr/bin/env bash
# Teammate one-time-per-machine: pull the shared bundle from tee-node, merge it into the
# local .env (backing up any existing one), drop the ABI + umbral state into place, then
# run doctor. Does NOT provision anything — teammates join the existing deployment.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
activate_venv
mkdir -p "$ROOT/kd" "$ROOT/out/ConfidentialCompute.sol"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

log "pulling shared config from tee-node:~/rmbs_cc_demo/share/ ..."
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  tee-node:~/rmbs_cc_demo/share/members.env "$TMP/members.env"
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  tee-node:~/rmbs_cc_demo/share/umbral_state.json "$ROOT/kd/umbral_state.json"
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  tee-node:~/rmbs_cc_demo/share/ConfidentialCompute.json \
  "$ROOT/out/ConfidentialCompute.sol/ConfidentialCompute.json"

# Shared values are the source of truth -> --force, but config_env still backs up .env first.
python config_env.py merge --from "$TMP/members.env" --into "$ROOT/.env" --force
log "config merged into .env (backup written). Running doctor..."
python doctor.py || warn "doctor reported failures — fix the FAILs above before 'make up'."
```

- [ ] **Step 4: Syntax-check all three**

Run: `bash -n ops/bootstrap.sh && bash -n ops/publish_config.sh && bash -n ops/sync.sh && echo syntax-ok`
Expected: `syntax-ok`.

- [ ] **Step 5: Commit**

```bash
git add ops/bootstrap.sh ops/publish_config.sh ops/sync.sh
git commit -m "feat: ops bootstrap/publish-config/sync — idempotent provisioning + member bundle"
```

---

## Task 10: `Makefile` + README pointer

**Files:**
- Create: `Makefile`
- Modify: `README.md`

- [ ] **Step 1: Write `Makefile`**

```makefile
# RMBS Confidential Compute — startup automation.
# Teammate flow:  make sync -> make up -> make demo -> make down
# Owner flow:     make infra-up -> make bootstrap -> make publish-config (... make infra-down)
.DEFAULT_GOAL := help
SHELL := /bin/bash

IAF ?= 500000
PAF ?= 1000000

.PHONY: help doctor sync up down status demo infra-up infra-down bootstrap publish-config

help: ## show this help
	@echo "Teammate:  make sync | up | demo | down | status | doctor"
	@echo "Owner:     make infra-up | infra-down | bootstrap | publish-config"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

doctor: ## preflight checks (read-only)
	@source .venv/bin/activate && python doctor.py

sync: ## teammate: pull shared config + artifacts, then doctor
	@bash ops/sync.sh

up: ## teammate: tunnels -> decryption nodes -> oracle agents
	@bash ops/up.sh

down: ## teammate: stop local processes (leaves shared infra running)
	@bash ops/down.sh

status: ## show tracked processes + chain/TEE reachability
	@bash ops/status.sh

demo: ## submit a request and read the result (override IAF=/PAF=)
	@source .venv/bin/activate && set -a && source .env && set +a && \
	  ID=$$(python submit_request.py --iaf $(IAF) --paf $(PAF) | tee /dev/stderr \
	        | grep -oE 'id=[0-9]+' | head -1 | cut -d= -f2) && \
	  [ -n "$$ID" ] && python read_result.py $$ID

infra-up: ## owner: start shared instances + remote TEE
	@bash ops/infra_up.sh

infra-down: ## owner: stop shared instances
	@bash ops/infra_down.sh

bootstrap: ## owner: idempotent ensure-provisioned (no-op when already done)
	@bash ops/bootstrap.sh

publish-config: ## owner: push config bundle to the shared tee-node
	@bash ops/publish_config.sh
```

- [ ] **Step 2: Verify help + target wiring**

Run: `make help`
Expected: prints the grouped header and the per-target descriptions (colorized), exit 0.

- [ ] **Step 3: Add a "Quick start (make)" section to `README.md`**

Near the top of `README.md`, add a short section pointing teammates at the new flow and noting `RUNBOOK.md` remains the manual fallback / troubleshooting reference:

```markdown
## Quick start (make)

Teammates sharing the existing cloud deployment:

```bash
source .venv/bin/activate     # see RUNBOOK stage 1 if .venv is missing
make sync     # one-time per machine: pull shared config + ABI + umbral state, run doctor
make up       # open tunnels, start decryption nodes + oracle agents (health-gated)
make demo     # submit a request and read the finalized result
make down     # stop local processes (shared infra keeps running)
```

Owner (manages the shared infra): `make infra-up`, `make bootstrap` (idempotent — safe to
re-run; no-op when already provisioned), `make publish-config`, `make infra-down`.
Run `make help` for all targets. `RUNBOOK.md` remains the manual procedure + troubleshooting.
```

- [ ] **Step 4: Commit**

```bash
git add Makefile README.md
git commit -m "feat: Makefile command surface + README quick start"
```

---

## Task 11: Acceptance — suite green + no-op verification + e2e

This task verifies spec §10. The no-op checks are the most important: they prove the
one-time/on-demand scripts do not disturb the already-provisioned environment.

- [ ] **Step 1: Full Python suite passes**

Run: `source .venv/bin/activate && python -m pytest tests/ -q`
Expected: PASS — the original 29 plus the new `config_env` (8), `provision_checks` (6), `run_oracle_agents` (3), `doctor` (5) tests.

- [ ] **Step 2: Forge tests still pass**

Run: `~/.foundry/bin/forge test -vv`
Expected: PASS (6 tests) — no contract changes were made.

- [ ] **Step 3: `doctor` is read-only (no mutation)**

With infra up + tunnels open:
Run: `git stash list >/dev/null; cp .env /tmp/env.before && make doctor; diff .env /tmp/env.before && echo "ENV-UNCHANGED"`
Expected: report prints; `ENV-UNCHANGED`; `git status` shows no new tracked changes.

- [ ] **Step 4: `bootstrap` is a no-op on the provisioned repo (the §2 property)**

With infra up + tunnels open, on the current repo (contract deployed, .env filled, umbral state present, oracles funded):
Run: `cp .env /tmp/env.before && make bootstrap`
Expected output contains all four skips:
`oracle keys present — skip generation`, `contract already provisioned on-chain — skip deploy`, `umbral state matches the live enclave key — skip keygen`, `all oracles funded — skip`, then `bootstrap complete.`
Run: `diff .env /tmp/env.before && echo "ENV-UNCHANGED" && ls .env.bak.* 2>/dev/null || echo "NO-BACKUP-CREATED"`
Expected: `ENV-UNCHANGED` and `NO-BACKUP-CREATED` (no change ⇒ no backup), and no new on-chain tx was sent.

- [ ] **Step 5: e2e via make**

Run: `make infra-up` (owner) then in the repo: `make up`, wait for "up complete", then `make demo`.
Expected: `read_result` prints `finalized=True` with `ClassA 79,000,000 / B 15,000,000 / C 5,000,000` (RUNBOOK stage 7 expected result).
Run: `make status` (shows RUNNING tunnels/decnodes/oracles), then `make down`.
Expected: `make status` afterward shows `.run` empty / all stopped; shared instances still reachable until `make infra-down`.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "test: startup automation acceptance — suite green + bootstrap no-op verified"
```

---

## Self-Review notes (verify during execution)

- **Spec §2 (no-clobber):** Task 2 tests prove `set_keys` skips/ backs up / preserves keys; Task 11 Step 4 proves the end-to-end no-op. ✔
- **Spec §7 guards:** each row maps to a `provision_checks.py` subcommand used in `ops/bootstrap.sh` Steps 1–4. ✔
- **Spec §4 surface:** every listed `make` target exists in Task 10. ✔
- **Spec §6 bundle:** `publish_config.sh` pushes all three files; `sync.sh` pulls all three (ABI → out/, umbral → kd/, env → merged). ✔
- **Type consistency:** `ORACLE_KEYS` is the single source the launcher (`run_oracle_agents.parse_oracle_keys`), doctor (`REQUIRED_ENV`), and provision_checks (`oracle_keys_present`) all read — same name everywhere. ✔
- **Naming:** `config_env.set_keys/merge_file`, `provision_checks.{contract_provisioned,under_funded,umbral_matches_enclave,oracle_keys_present}`, `run_oracle_agents.{parse_oracle_keys,build_commands}`, `doctor.{check,check_env_keys,check_url,format_report,run_all}` — referenced consistently across tasks and scripts. ✔
