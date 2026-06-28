# TEE Boot-Time systemd Service — Design Spec

**Date:** 2026-06-27
**Status:** Design approved, ready for implementation plan
**Implements:** decoupling the TEE start from an owner SSH session so teammates with only
start/stop + IAP-tunnel access can run the full demo (no owner intervention to start the TEE).
**Reference:** `docs/superpowers/specs/2026-06-26-startup-automation-design.md` (the `make`
flow this extends); `docs/TROUBLESHOOTING.md` (the OS-Login / home-dir footgun this removes).

---

## 1. Goal

Today `make infra-up` SSHes into `tee-node` and starts the TEE in a `tmux` session **from the
owner's home directory**. That means:
- a teammate running `make infra-up` lands in a *different* (empty) home over SSH, so
  `cd ~/rmbs_cc_demo && python -m tee.tee_service` fails — the TEE can only be started by the
  owner; and
- if the owner isn't around to start it, nobody can run the demo.

Make the TEE a **boot-time systemd service** on `tee-node`. Then "start the VM" is all anyone
needs: the TEE comes up automatically, no SSH into a home directory, no `tmux`. A teammate
with start/stop + IAP-tunnel access can run the entire flow themselves.

## 2. Hard constraint (must not break)

The TEE's ECDSA **signing key** lives in `tee/kd/tee_signing_key.json` (loaded relative to the
`tee/` package). Its address is `TEE_ADDRESS`, baked into the **already-deployed** contract.
The enclave receiving key is in `tee/kd/enclave_enc_key.json`, pinned by the umbral kfrags.

> **The service must run from the existing install so both key files are read unchanged.**
> `TEE_ADDRESS` and the enclave pubkey stay identical → no contract redeploy, no re-keygen.

This is why the design is **in-place** (run as the owner, from the owner's existing
`~/rmbs_cc_demo`) rather than moving code to `/opt` under a new user.

## 3. Scope and non-goals

**In scope**
- A systemd unit `rmbs-tee.service` that auto-starts the TEE on boot, running as the owner
  from the existing repo in their home.
- A one-time owner installer (`ops/install_tee_service.sh` + `make tee-install`).
- Reworking `make infra-up` to drop the SSH/`tmux` TEE-start step.
- Owner targets `make tee-deploy` (safe code push + restart), `make tee-restart`, `make tee-logs`.
- Doc updates (`CLAUDE.md`, `docs/TROUBLESHOOTING.md`).

**Out of scope (deliberate)**
- Moving code/venv/keys to `/opt` or a dedicated system user (rejected: needless risk to the
  signing key for cosmetic gain).
- Changing TEE code, ports (stays `0.0.0.0:8000`), or the key files.
- Implementing IAM changes — the least-privilege role recommendation (§5) is **advisory text
  in this spec only**, not a code change.
- Remote **dependency** installs (`pip install` when `requirements.txt` changes for the TEE)
  stay a manual owner step; `make tee-deploy` pushes **code only** (`.py`), not dependencies.

## 4. Components

### 4.1 `rmbs-tee.service` (systemd system unit)

Installed at `/etc/systemd/system/rmbs-tee.service`, with `<user>` and `<home>` filled in by
the installer from the owner's SSH identity (`whoami` / `$HOME`):

```ini
[Unit]
Description=RMBS Confidential Compute TEE enclave
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<user>
WorkingDirectory=<home>/rmbs_cc_demo
ExecStart=<home>/rmbs_cc_demo/.venv/bin/python -m tee.tee_service
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- `User=<owner>` + `WorkingDirectory=<home>/rmbs_cc_demo` → reads the existing code, `.venv`,
  `tee/kd/*.json`, and `kd/umbral_state.json` exactly as the manual run does today.
- `WantedBy=multi-user.target` → starts on every boot (including a fresh VM start).
- `Restart=on-failure` → self-heals a crash.

### 4.2 Installer: `ops/install_tee_service.sh` + `make tee-install`

`make tee-install` (owner, one-time / on unit change): scp `ops/install_tee_service.sh` to
`tee-node`, then run it there over IAP SSH. The script, executed **on tee-node as the owner**:

1. Derive `USER="$(whoami)"`, `HOME_DIR="$HOME"`, `REPO="$HOME/rmbs_cc_demo"`; verify
   `$REPO/.venv/bin/python` and `$REPO/tee/tee_service.py` exist (fail clearly otherwise).
2. **Migration:** `tmux kill-session -t tee 2>/dev/null || true` to free port 8000 from any
   manually-started TEE.
3. Render the unit (substituting user/home) and `sudo tee /etc/systemd/system/rmbs-tee.service`.
4. `sudo systemctl daemon-reload && sudo systemctl enable --now rmbs-tee`.
5. Self-check: poll `curl -sf http://127.0.0.1:8000/tee_address` and print the address.

Idempotent (re-running re-writes the unit and restarts cleanly).

### 4.3 `make infra-up` rework

Remove the `gcloud compute ssh tee-node … tmux new-session …` block entirely. `infra_up.sh`
becomes: start the 7 instances (unchanged best-effort, tolerating a validator zone-capacity
failure), then print that the TEE auto-starts on boot and `make up` will verify it. **No SSH.**
The TEE's liveness is confirmed by `make up`'s existing TEE health-gate (`wait_for "TEE
service"`), so no verification logic is duplicated in `infra-up`.

### 4.4 Owner targets (deploy / restart / logs)

All owner-only (need SSH + sudo); members never use them. Each is an `ops/*.sh` script
sourcing `ops/lib.sh` for `$ZONE_A`, consistent with the other ops targets and `bash -n`-checkable.

- `make tee-deploy` (`ops/tee_deploy.sh`) — push updated TEE code to `tee-node` and restart.
  **Safe by construction:** it scp's **only the `.py` files** the TEE needs and **never
  `tee/kd/`** (recursing or copying `tee/kd/` would clobber the remote signing/enclave keys →
  `TEE_ADDRESS` changes → contract redeploy). Concretely:
  - `tee/*.py` → `tee-node:~/rmbs_cc_demo/tee/`
  - `tee/engine/*.py` → `tee-node:~/rmbs_cc_demo/tee/engine/`
  - `abi_digest.py`, `umbral_io.py` → `tee-node:~/rmbs_cc_demo/`
  - then `sudo systemctl restart rmbs-tee` and a `curl …/tee_address` self-check.
  (Globs are non-recursive, so `tee/*.py` matches no file under `tee/kd/`.) If a change also
  touches `requirements.txt`, install the dep on `tee-node` manually first (out of scope, §3).
- `make tee-restart` (`ops/tee_restart.sh`) → `sudo systemctl restart rmbs-tee` (restart without
  pushing code).
- `make tee-logs` (`ops/tee_logs.sh`) → `journalctl -u rmbs-tee -n 50 --no-pager` (debug a TEE
  that won't come up).

### 4.5 Migration (one-time, owner)

The TEE currently runs in the owner's `tmux`. `make tee-install` kills that session (step 4.2.2)
before enabling the service, so port 8000 hands off cleanly. Because the same `tee/kd/*.json`
files are read, `TEE_ADDRESS` and the enclave pubkey are unchanged — nothing on-chain moves.

## 5. Member access model (advisory, not implemented)

With the TEE auto-starting, a teammate needs only: **start/stop the instances + open IAP
tunnels.** Recommended IAM (replacing the broad *Compute Instance Admin (v1)*, which also
grants `compute.instances.delete` — deleting `tee-node` is the catastrophic action that loses
the signing key):

- a **custom role** with `compute.instances.{start,stop,get,list}`, `compute.zones.get`;
- **IAP-secured Tunnel User** (`roles/iap.tunnelResourceAccessor`);
- **Service Account User** (`roles/iam.serviceAccountUser`) for `actAs` on instance start.

This removes "can delete the TEE node" from members while preserving start/stop. Recorded as
guidance; granting roles is a console/gcloud action outside this repo.

## 6. Documentation updates

- `CLAUDE.md`: the TEE runs as the `rmbs-tee` systemd service (not a manual `tmux`); `make
  infra-up` no longer SSHes; add `make tee-install/restart/logs`.
- `docs/TROUBLESHOOTING.md`: "TEE unreachable" → `make tee-logs` / `systemctl status
  rmbs-tee`; note the OS-Login/home-dir footgun is now moot for members (they don't SSH into
  `tee-node`); `make tee-install` is the one-time owner setup.

## 7. Testing / acceptance

- `bash -n` on every new/changed `ops/*.sh`; `make help` lists the new targets.
- No Python changes → `pytest tests/` (67) and `forge test` (6) unaffected.
- **Live acceptance (owner, once):**
  1. `make tee-install` → prints the TEE address; `systemctl is-enabled rmbs-tee` = `enabled`.
  2. `make infra-down` then `make infra-up` (cold VM start) → wait → `make up`: the TEE
     health-gate goes green **without anyone SSHing to start it**.
  3. `curl …/tee_address` returns the **same** `TEE_ADDRESS` as before (key preserved);
     `make demo` finalizes 3/3 with the expected waterfall.
  4. A teammate (start/stop + IAP-tunnel only, no SSH-to-start) can run `make infra-up` →
     `make up` → `make demo` end to end.

## 8. Files touched

**New:** `ops/install_tee_service.sh`, `ops/tee_deploy.sh`, `ops/tee_restart.sh`,
`ops/tee_logs.sh`, and the rendered `rmbs-tee.service` template (embedded in the installer).
**Modified:** `ops/infra_up.sh` (drop SSH/tmux block), `Makefile` (add `tee-install`,
`tee-deploy`, `tee-restart`, `tee-logs`; `.PHONY`), `CLAUDE.md`, `docs/TROUBLESHOOTING.md`
(add an "updating TEE code" note pointing at `make tee-deploy`).
