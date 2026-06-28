# TEE Boot-Time systemd Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the TEE auto-start on `tee-node` boot via a systemd service so teammates with only start/stop + IAP-tunnel access can run the full demo, and `make infra-up` no longer SSHes into a home directory to start it.

**Architecture:** A systemd system unit `rmbs-tee.service` runs the TEE **in place** — as the owner, from the existing `~/rmbs_cc_demo` install — so `tee/kd/*.json` (the signing key behind `TEE_ADDRESS`) is read unchanged. A one-time owner installer pushes + enables it; owner targets manage code deploy/restart/logs; `make infra-up` drops its SSH/tmux block. No Python changes.

**Tech Stack:** systemd, Bash, GNU Make, `gcloud` (IAP SSH/scp). No unit-test framework for bash — gate is `bash -n` + `make help` + a final live acceptance on real infra.

**Spec:** `docs/superpowers/specs/2026-06-27-tee-systemd-autostart-design.md` — read §2 (TEE_ADDRESS hard constraint) and §4 before starting.

---

## File Structure

**New:**
- `ops/install_tee_service.sh` — runs **on tee-node**; renders + installs + enables the unit (idempotent).
- `ops/tee_install.sh` — local orchestrator for `make tee-install`: scp the installer to tee-node, run it.
- `ops/tee_deploy.sh` — safe TEE code push (`.py` only, never `tee/kd/`) + restart.
- `ops/tee_restart.sh` — restart the service only.
- `ops/tee_logs.sh` — tail `journalctl -u rmbs-tee`.

**Modified:**
- `ops/infra_up.sh` — remove the SSH/tmux TEE-start block.
- `Makefile` — add `tee-install`, `tee-deploy`, `tee-restart`, `tee-logs` + `.PHONY`.
- `CLAUDE.md` — TEE is the `rmbs-tee` systemd service; `infra-up` no longer SSHes; record the `tee/kd/` deploy footgun.
- `docs/TROUBLESHOOTING.md` — "TEE unreachable" via systemd; "updating TEE code" via `make tee-deploy` (with the footgun).

All `ops/*.sh` source `ops/lib.sh` (provides `log`/`warn`/`die`, `ROOT`, `ZONE_A`). Owner targets need IAP SSH + sudo on tee-node; members never run them.

---

## Task 1: The systemd installer (`ops/install_tee_service.sh`)

**Files:**
- Create: `ops/install_tee_service.sh`

This script runs **on tee-node** (it is scp'd there in Task 2). It must be self-contained (it does not source `ops/lib.sh`, which isn't present on tee-node).

- [ ] **Step 1: Create `ops/install_tee_service.sh`**

```bash
#!/usr/bin/env bash
# Runs ON tee-node (pushed by `make tee-install`). Installs + enables the rmbs-tee systemd
# service so the TEE auto-starts on boot. Run as the owner (the SSH user); idempotent.
# Renders the unit from the owner's own identity/paths so nothing is hardcoded.
set -euo pipefail

USER_NAME="$(whoami)"
REPO="$HOME/rmbs_cc_demo"
PY="$REPO/.venv/bin/python"

[ -x "$PY" ] || { echo "ERROR: $PY not found — repo + venv must exist in $HOME"; exit 1; }
[ -f "$REPO/tee/tee_service.py" ] || { echo "ERROR: $REPO/tee/tee_service.py not found"; exit 1; }

# Hand off port 8000 from any manually-started (tmux) TEE before enabling the service.
tmux kill-session -t tee 2>/dev/null || true

echo "writing /etc/systemd/system/rmbs-tee.service ..."
sudo tee /etc/systemd/system/rmbs-tee.service >/dev/null <<EOF
[Unit]
Description=RMBS Confidential Compute TEE enclave
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$REPO
ExecStart=$PY -m tee.tee_service
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now rmbs-tee

echo "waiting for the TEE to answer ..."
for _ in $(seq 1 10); do
  if curl -sf http://127.0.0.1:8000/tee_address; then
    echo; echo "rmbs-tee is up and enabled on boot."; exit 0
  fi
  sleep 2
done
echo "WARNING: rmbs-tee did not answer /tee_address yet — check 'sudo systemctl status rmbs-tee'"
exit 1
```

- [ ] **Step 2: Syntax-check**

Run: `bash -n ops/install_tee_service.sh && echo syntax-ok`
Expected: `syntax-ok`

- [ ] **Step 3: Commit**

```bash
git add ops/install_tee_service.sh
git commit -m "feat: ops/install_tee_service.sh — install+enable rmbs-tee on tee-node"
```
End the commit message with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 2: Local orchestrator (`ops/tee_install.sh`)

**Files:**
- Create: `ops/tee_install.sh`

- [ ] **Step 1: Create `ops/tee_install.sh`**

```bash
#!/usr/bin/env bash
# Owner one-time: push the installer to tee-node and run it (installs + enables rmbs-tee).
# Needs IAP SSH + sudo on tee-node. The TEE runs in place from your ~/rmbs_cc_demo, so the
# existing tee/kd/ signing key is read unchanged (TEE_ADDRESS does not move).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "pushing installer to tee-node ..."
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/ops/install_tee_service.sh" tee-node:~/install_tee_service.sh

log "running installer on tee-node (will prompt for nothing; uses sudo) ..."
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='bash ~/install_tee_service.sh'

log "tee-install done — the TEE now auto-starts on boot (rmbs-tee.service)."
```

- [ ] **Step 2: Syntax-check**

Run: `bash -n ops/tee_install.sh && echo syntax-ok`
Expected: `syntax-ok`

- [ ] **Step 3: Commit**

```bash
git add ops/tee_install.sh
git commit -m "feat: ops/tee_install.sh — push+run the rmbs-tee installer (make tee-install)"
```
End the commit message with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 3: Owner deploy/restart/logs scripts

**Files:**
- Create: `ops/tee_deploy.sh`, `ops/tee_restart.sh`, `ops/tee_logs.sh`

- [ ] **Step 1: Create `ops/tee_deploy.sh`**

```bash
#!/usr/bin/env bash
# Owner: push updated TEE *code* (.py only) to tee-node and restart the service.
#
# SAFETY (do not "simplify" this): it copies ONLY .py files and NEVER tee/kd/. Copying
# tee/kd/ (e.g. via `scp --recurse tee/`) overwrites the remote signing + enclave keys,
# which changes TEE_ADDRESS and forces a contract redeploy + re-keygen. The globs below are
# non-recursive, so `tee/*.py` cannot match anything under tee/kd/.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "pushing tee/*.py + tee/engine/*.py + abi_digest.py/umbral_io.py (NOT tee/kd/) ..."
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT"/tee/*.py tee-node:~/rmbs_cc_demo/tee/
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT"/tee/engine/*.py tee-node:~/rmbs_cc_demo/tee/engine/
gcloud compute scp --tunnel-through-iap --zone="$ZONE_A" \
  "$ROOT/abi_digest.py" "$ROOT/umbral_io.py" tee-node:~/rmbs_cc_demo/

log "restarting rmbs-tee ..."
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='sudo systemctl restart rmbs-tee && sleep 2 && curl -sf http://127.0.0.1:8000/tee_address && echo'

log "tee-deploy done (tee/kd/ untouched). If requirements.txt changed, pip install on tee-node first."
```

- [ ] **Step 2: Create `ops/tee_restart.sh`**

```bash
#!/usr/bin/env bash
# Owner: restart the TEE service (no code push).
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='sudo systemctl restart rmbs-tee && sleep 2 && systemctl is-active rmbs-tee'
log "rmbs-tee restarted."
```

- [ ] **Step 3: Create `ops/tee_logs.sh`**

```bash
#!/usr/bin/env bash
# Owner: show the last 50 lines of the TEE service log.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
gcloud compute ssh tee-node --zone="$ZONE_A" --tunnel-through-iap \
  --command='journalctl -u rmbs-tee -n 50 --no-pager'
```

- [ ] **Step 4: Syntax-check all three**

Run: `bash -n ops/tee_deploy.sh && bash -n ops/tee_restart.sh && bash -n ops/tee_logs.sh && echo syntax-ok`
Expected: `syntax-ok`

- [ ] **Step 5: Commit**

```bash
git add ops/tee_deploy.sh ops/tee_restart.sh ops/tee_logs.sh
git commit -m "feat: ops tee-deploy/restart/logs — owner TEE service management (safe deploy)"
```
End the commit message with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 4: Drop the SSH/tmux TEE-start from `make infra-up`

**Files:**
- Modify: `ops/infra_up.sh`

- [ ] **Step 1: Read the current file**

Run: `cat ops/infra_up.sh`
Expected: it starts the instances (best-effort with `|| warn`), then has a `log "ensuring the TEE service is up ..."` line followed by a `TEE_CMD='...'` block and a `for attempt in $(seq 1 8); do ... gcloud compute ssh ... done` retry loop, then a final `log "infra-up done. ..."`.

- [ ] **Step 2: Remove the TEE-start block**

Delete everything from the line `log "ensuring the TEE service is up on tee-node (tmux session 'tee')..."` through the end of the `for attempt ... done` loop (the entire SSH/TEE_CMD section), and replace the final `log "infra-up done. ..."` line. The file should end up as exactly:

```bash
#!/usr/bin/env bash
# Owner: start the shared instances. The TEE auto-starts on boot via the rmbs-tee systemd
# service (see `make tee-install`), so no SSH into tee-node is needed here — `make up`'s
# health-gate verifies the TEE once tunnels are open.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log "starting shared instances (QBFT needs >=3 validators online to produce blocks)..."
# Best-effort: a single zone running out of capacity (ZONE_RESOURCE_POOL_EXHAUSTED) must
# not abort startup — QBFT tolerates one validator down (3 of 4). 'make up' / doctor verify
# actual block production + TEE reachability afterwards.
gcloud compute instances start $INSTANCES_A --zone="$ZONE_A" \
  || warn "some instances in $ZONE_A failed to start (capacity?) — continuing"
gcloud compute instances start $INSTANCES_B --zone="$ZONE_B" \
  || warn "some instances in $ZONE_B failed to start (capacity?) — continuing"
gcloud compute instances start $INSTANCES_C --zone="$ZONE_C" \
  || warn "some instances in $ZONE_C failed to start (capacity?) — continuing; chain still runs if >=3 validators are up"

log "infra-up done. The TEE auto-starts on boot; run 'make up' to open tunnels + verify it."
```

- [ ] **Step 3: Syntax-check**

Run: `bash -n ops/infra_up.sh && echo syntax-ok`
Expected: `syntax-ok`

- [ ] **Step 4: Confirm no SSH/tmux remains**

Run: `grep -nE 'ssh|tmux|TEE_CMD|tee_address' ops/infra_up.sh || echo "clean: no SSH/tmux/TEE in infra-up"`
Expected: `clean: no SSH/tmux/TEE in infra-up`

- [ ] **Step 5: Commit**

```bash
git add ops/infra_up.sh
git commit -m "feat: infra-up no longer SSHes to start the TEE (now a boot-time systemd service)"
```
End the commit message with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 5: Makefile targets

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Add the four targets to `.PHONY`**

Find the `.PHONY:` line (it lists `help doctor sync up down status demo result infra-up infra-down bootstrap publish-config`). Replace it with:

```makefile
.PHONY: help doctor sync up down status demo result infra-up infra-down bootstrap publish-config tee-install tee-deploy tee-restart tee-logs
```

- [ ] **Step 2: Add the four target recipes**

Append these to the end of the `Makefile` (recipe lines are TAB-indented):

```makefile

tee-install: ## owner: install+enable the rmbs-tee systemd service on tee-node (one-time)
	@bash ops/tee_install.sh

tee-deploy: ## owner: push updated TEE .py code to tee-node + restart (never touches tee/kd/)
	@bash ops/tee_deploy.sh

tee-restart: ## owner: restart the rmbs-tee service
	@bash ops/tee_restart.sh

tee-logs: ## owner: tail the rmbs-tee service logs
	@bash ops/tee_logs.sh
```

- [ ] **Step 3: Verify the targets appear and expand**

Run: `make help | grep -E 'tee-install|tee-deploy|tee-restart|tee-logs'`
Expected: four lines, one per new target, with their `##` descriptions.

Run: `make -n tee-deploy`
Expected: prints `bash ops/tee_deploy.sh` (no error).

- [ ] **Step 4: Commit**

```bash
git add Makefile
git commit -m "feat: Makefile — tee-install/deploy/restart/logs targets"
```
End the commit message with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 6: Documentation — record the `tee/kd/` footgun + the new flow

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/TROUBLESHOOTING.md`

- [ ] **Step 1: Update the TEE description in `CLAUDE.md`**

Find the `tee/` bullet in the "Component map" (it begins `` - `tee/` — the compute enclave (FastAPI). ``). At the end of that bullet's text, before the next component bullet, add a sentence:

```
  On `tee-node` the service runs as the **`rmbs-tee` systemd service** (auto-starts on boot;
  installed once via `make tee-install`), not a manual `tmux` session; `make infra-up` no
  longer SSHes to start it. Push TEE code changes with `make tee-deploy` (restart via
  `make tee-restart`, logs via `make tee-logs`).
```

- [ ] **Step 2: Add the deploy footgun to CLAUDE.md's fragility section**

Find the section header `### The single most fragile thing: the cross-language signing seam`. Immediately **before** that header, insert a new short subsection:

```
### Deploying TEE code: never copy `tee/kd/` to `tee-node`

`tee/kd/tee_signing_key.json` (whose address is the on-chain `TEE_ADDRESS`) and
`tee/kd/enclave_enc_key.json` live **only** on `tee-node` and must never be overwritten by a
local copy. `scp --recurse tee/` or copying `tee/kd/` from your machine replaces them →
`TEE_ADDRESS` changes → the deployed contract must be redeployed and umbral keys regenerated.
Always deploy TEE code with **`make tee-deploy`**, which copies only `.py` files (non-recursive
globs, never `tee/kd/`). This is a recorded landmine: any future TEE deploy must respect it.

```

- [ ] **Step 3: Add an "updating TEE code" note to `docs/TROUBLESHOOTING.md`**

In `docs/TROUBLESHOOTING.md`, under the `## TEE / enclave` section, append these bullets:

```
- **TEE unreachable but tunnel is open** → the `rmbs-tee` service may be down on `tee-node`.
  Check `make tee-logs` (or `sudo systemctl status rmbs-tee` on the node); `make tee-restart`
  to bounce it. The service auto-starts on boot; `make infra-up` no longer starts it over SSH,
  so a member who can start the VM no longer needs SSH access to bring the TEE up.
- **Updating TEE code** → edit `tee/*.py` (or `abi_digest.py`/`umbral_io.py`) locally, then
  `make tee-deploy` (pushes `.py` only + restarts). **Never** `scp --recurse tee/` or copy
  `tee/kd/` to the node — that overwrites the remote signing/enclave keys, changing
  `TEE_ADDRESS` and forcing a contract redeploy. If `requirements.txt` changed, `pip install`
  on `tee-node` first, then `make tee-restart`.
```

- [ ] **Step 4: Verify docs reference real targets**

Run: `grep -c 'tee-deploy' CLAUDE.md docs/TROUBLESHOOTING.md`
Expected: a nonzero count in each file.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/TROUBLESHOOTING.md
git commit -m "docs: TEE-as-systemd flow + record the tee/kd deploy footgun"
```
End the commit message with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

## Task 7: Acceptance

- [ ] **Step 1: Offline gates**

Run: `for f in ops/*.sh; do bash -n "$f" || echo "FAIL $f"; done && echo all-ops-ok`
Expected: `all-ops-ok`

Run: `make help`
Expected: lists `tee-install`, `tee-deploy`, `tee-restart`, `tee-logs` among the targets.

Run: `source .venv/bin/activate && python -m pytest tests/ -q && ~/.foundry/bin/forge test`
Expected: pytest 67 passed, forge 6 passed (no code changed — these must be unaffected).

- [ ] **Step 2: Live acceptance (owner, on real infra)**

Record the current address first: `cast call "$CONTRACT_ADDRESS" "teeAddress()(address)" --rpc-url "$RPC_URL"` (or note `.env` `TEE_ADDRESS`). Then:

```bash
make tee-install                 # installs+enables rmbs-tee; prints the TEE address
make infra-down && make infra-up # cold VM start; infra-up does NOT ssh-start the TEE now
make up                          # TEE health-gate must go green WITHOUT anyone SSHing to start it
```

Expected: `make up`'s `ready: TEE service` appears with no manual TEE start. Confirm the address is unchanged:

```bash
curl -s http://127.0.0.1:8000/tee_address   # must equal the TEE_ADDRESS recorded above
make demo                                    # finalized 3/3, ClassA 79,000,000 / B 15,000,000 / C 5,000,000
```

- [ ] **Step 3: Verify `tee-deploy` safety on the node (owner)**

After a `make tee-deploy`, confirm the remote keys are intact (address still matches):

```bash
make tee-deploy
curl -s http://127.0.0.1:8000/tee_address   # still the same TEE_ADDRESS
```

Expected: identical address → `tee/kd/` was not clobbered.

- [ ] **Step 4: Final commit (if any acceptance fixups were needed)**

```bash
git add -A && git commit -m "test: TEE systemd autostart acceptance"
```
(Skip if nothing changed.)

---

## Self-Review notes

- **Spec §2 (TEE_ADDRESS preserved):** Task 1 runs the service from the existing `~/rmbs_cc_demo` (keys unchanged); Task 3's `tee-deploy` copies only `.py`; Task 7 Steps 2–3 verify the address is unchanged. ✔
- **Spec §4.1 unit / §4.2 installer / §4.3 infra-up / §4.4 deploy+restart+logs / §4.5 migration (tmux kill):** Tasks 1, 2/1, 4, 3, 1(Step1 `tmux kill-session`) respectively. ✔
- **Spec §6 docs + footgun:** Task 6. **Spec §5 IAM:** advisory-only, no task (as specified). ✔
- **Naming consistency:** service `rmbs-tee`, targets `tee-install`/`tee-deploy`/`tee-restart`/`tee-logs`, scripts `ops/{install_tee_service,tee_install,tee_deploy,tee_restart,tee_logs}.sh` — used identically across tasks. ✔
- **No Python touched** → existing test suites are the regression guard (Task 7 Step 1). ✔
