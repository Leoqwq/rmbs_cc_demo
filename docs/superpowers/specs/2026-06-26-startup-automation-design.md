# Startup Automation (Makefile + ops scripts) — Design Spec

**Date:** 2026-06-26
**Status:** Design approved, ready for implementation plan
**Implements:** operational pain recorded in `RUNBOOK.md` — the ~10-terminal, strict-ordering
manual startup that blocks teammates from reproducing the demo locally.
**Reference:** `RUNBOOK.md` (authoritative manual procedure this automates).

---

## 1. Goal

Collapse the manual startup in `RUNBOOK.md` (≈10 terminals, dozens of ordered commands,
16 documented foot-guns) into a small, self-documenting command surface so a **teammate
who shares the existing cloud deployment** can reproduce the end-to-end demo with a few
`make` targets instead of the full runbook.

Two audiences, two paths:

- **Owner** (you — owns the shared cloud infra): one idempotent `bootstrap` for first-time /
  on-demand provisioning, plus shared-instance lifecycle.
- **Teammate** (joins the *existing* shared deployment): they must **not** re-provision
  (re-deploying the contract, regenerating keys, or re-running keygen would fragment the
  shared on-chain/enclave state). Their first-run step is **`sync`** — pull the shared
  config + artifacts and health-check the local toolchain — not `bootstrap`.

## 2. The non-negotiable safety property

> **Running any one-time / on-demand script against an already-provisioned environment
> must be a no-op for state and must leave every subsequent startup script working.**

This is the user's explicit requirement and the design's central constraint. Concretely:

- Every **mutating** provisioning step (deploy, keygen, generate oracle keys, fund) is
  **guarded by a probe** of current state and **skips** when already satisfied.
- All `.env` writes are **merge, not replace**: set-a-key-only-if-absent by default,
  never drop or silently overwrite an existing key, and always write a timestamped backup
  first. Overwriting an existing value requires an explicit `--force`.
- `bootstrap` run on a fully-provisioned repo (the current state: contract deployed,
  `.env` filled, keys present) prints "already provisioned" and changes nothing on disk
  or chain.

## 3. Scope and non-goals

**In scope**
- A `Makefile` command surface (the single thing teammates learn).
- Owner targets: `bootstrap` (idempotent ensure-provisioned), `publish-config`,
  `infra-up` / `infra-down` (shared instances + remote TEE).
- Teammate targets: `sync` (pull config + doctor), `up` (tunnels → decryption nodes →
  oracle agents, with health gates), `demo` (submit + read), `down` (kill local procs).
- `doctor` preflight: actionable pass/fail for every prerequisite.
- A multi-oracle launcher so 4 agent terminals collapse to one background process group.
- Background process + tunnel lifecycle via a `.run/` dir (PIDs + logs).

**Out of scope (stays manual / documented, cannot be safely automated)**
- Local toolchain install: `gcloud` SDK, Python `python3-venv`, (owner-only) Foundry.
- `gcloud auth login` and IAP/IAM permission grants (per-person, org-level).
- The genesis `DEPLOYER_PRIVATE_KEY` secret — must be provisioned out-of-band into the
  owner's `.env`; teammates receive it via `sync` (see §6 security note).
- OS-Login / node-rebuild recovery (the `RUNBOOK` edge cases) — left as documented manual
  recovery; out of the happy path.
- The demo interaction itself (`submit_request` / `read_result`) stays an explicit user
  action; `make demo` is a thin convenience wrapper, not hidden magic.

## 4. Command surface (the deliverable, from the user's view)

```
make help            # list targets, grouped owner vs teammate
make doctor          # preflight: gcloud auth, venv+deps, .env completeness,
                     #   chain reachable, TEE reachable, decryption nodes — actionable

# ---- teammate (joins the shared deployment) ----
make sync            # one-time per machine: pull members.env + umbral_state.json + ABI
                     #   from the shared tee-node, back up any existing .env, run doctor
make up              # open 2 tunnels → start N decryption nodes → start N oracle agents,
                     #   gating on health between each stage; logs+PIDs in .run/
make demo IAF=500000 PAF=1000000   # submit_request then read_result
make down            # kill agents, decryption nodes, tunnels (does NOT touch shared infra)
make status          # show what .run/ has running + chain/TEE reachability

# ---- owner (manages shared cloud) ----
make infra-up        # start the 7 shared instances, wait for block production,
                     #   start the TEE in tmux on tee-node (idempotent)
make infra-down      # stop the shared instances
make bootstrap       # idempotent ensure-provisioned (deploy/keygen/keys/fund) — no-op
                     #   when already done; never clobbers existing .env / contract
make publish-config  # push current .env + kd/umbral_state.json + ABI to tee-node share dir
```

## 5. Architecture

Thin **bash glue under `ops/`** (for the inherently-shell parts: `gcloud`, tunnels, tmux,
scp, forge) driven by a root **`Makefile`**, delegating the **stateful, must-be-correct
logic to small, unit-tested Python helpers** (peers of the existing `chain.py` /
`umbral_io.py`, reusing them):

| File | Kind | Responsibility |
|---|---|---|
| `Makefile` | new | user-facing target surface; `make help` self-doc; delegates to `ops/*.sh` |
| `ops/lib.sh` | new bash | shared helpers: load `.env`, instance/zone table, `.run/` PID+log mgmt, `wait_for` health poller, logging |
| `ops/infra_up.sh` | new bash | owner: start instances → wait blocks → start remote TEE in tmux (probe-first, idempotent) |
| `ops/infra_down.sh` | new bash | owner: stop instances |
| `ops/bootstrap.sh` | new bash | owner: orchestrate probes (via `provision_checks.py`) + deploy/keygen/keys/fund, each guarded |
| `ops/publish_config.sh` | new bash | owner: scp `.env`+`umbral_state.json`+ABI to tee-node `~/rmbs_cc_demo/share/` |
| `ops/sync.sh` | new bash | teammate: scp those down, back up local `.env`, merge via `config_env.py`, run doctor |
| `ops/up.sh` | new bash | teammate: open tunnels (bg) → wait → decryption nodes (bg) → wait → oracle agents (bg) |
| `ops/down.sh` | new bash | teammate: kill tracked `.run/*.pid` |
| `config_env.py` | new py (TDD) | safe `.env` parse / set-if-absent / atomic write / backup. The heart of "don't clobber". |
| `provision_checks.py` | new py (TDD) | idempotency probes: `contract_provisioned()`, `oracles_funded()`, `umbral_matches_enclave()` |
| `doctor.py` | new py (TDD) | preflight checks → structured results, human-readable report, nonzero exit on fail |
| `run_oracle_agents.py` | new py (TDD) | launch one `oracle_agent.py` subprocess per key in `ORACLE_KEYS`, prefixed logs |

**Modified:** `.env.example` (add `ORACLE_KEYS`, owner/teammate sections, share-dir note);
`.gitignore` (add `.run/`, `.env.bak.*`); `README.md` (point at `make` targets);
`docs/FUTURE_WORK.md` (record the shared-deployer-key simplification).

### 5.1 Why the Python/bash split

The parts that can corrupt the shared deployment if they're wrong — deciding whether to
deploy, merging `.env` without dropping keys — go in **Python where they are unit-tested**
(the repo gate is `pytest`). Bash stays thin orchestration with manual `make`-level
verification. No bash unit-test framework is introduced.

### 5.2 `.run/` runtime dir (gitignored)

`up` writes `tunnel-chain.pid`, `tunnel-tee.pid`, `decnode-<port>.pid`,
`oracle-<id>.pid` and matching `.log` files into `.run/`. `down` reads and kills them.
`status` reports them. This gives clean lifecycle and a place to look when something fails
(directly addresses "teammates can't tell where it broke").

## 6. Config distribution (decided: scp from the shared tee-node)

Teammates already must reach `tee-node` over IAP to tunnel to the TEE, so `sync` reuses
exactly that access — no new bucket/IAM to maintain, and `umbral_state.json` is already
synced there by the existing flow.

- Owner `publish-config` writes a member bundle to `tee-node:~/rmbs_cc_demo/share/`:
  `members.env`, `umbral_state.json`, `ConfidentialCompute.json` (the ABI — gitignored,
  so it must travel here since teammates are foundry-free).
- Teammate `sync` pulls the three down: ABI → `out/ConfidentialCompute.sol/`,
  `umbral_state.json` → `kd/`, `members.env` → merged into local `.env` (after backup).

**Security note (record in FUTURE_WORK):** the member bundle includes the shared
`DEPLOYER_PRIVATE_KEY` and the 4 oracle keys (teammates need a funded account to submit
and the oracle identities to run agents). For a private-chain demo this is accepted; a
real deployment would issue per-member funded accounts and use a secret manager rather
than sharing the genesis key. Also note: `ORACLE_KEYS` lands on each member's disk.

## 7. Idempotency guards (the §2 property, concretely)

| Step | Probe (skip when true) | If probe false |
|---|---|---|
| Deploy contract | `.env CONTRACT_ADDRESS` set **and** on-chain `oracleCount()==len(ORACLE_ADDRESSES)` **and** `teeAddress()==TEE_ADDRESS` **and** `threshold()==THRESHOLD` | deploy, then `config_env` set-if-absent `CONTRACT_ADDRESS` (refuse to overwrite a mismatched non-empty one without `--force`) |
| Generate oracle keys | `.env` has non-empty `ORACLE_ADDRESSES` **and** `ORACLE_KEYS` of equal length | generate, write both (set-if-absent) |
| keygen (umbral) | `kd/umbral_state.json` exists **and** its `enclave_public_key` == live `GET /enclave_pubkey` | run keygen, then `publish-config` to push state to TEE |
| Fund oracles | every oracle balance ≥ `ORACLE_FUND_ETHER` floor | top up only those below floor |
| `.env` write-back | key already present | append key (set-if-absent); never reorder/drop existing keys; `.env.bak.<ts>` first |

Re-running `bootstrap` in the current (fully provisioned) repo → all probes true → it
prints a summary and exits without a single state change. This is the property to test
explicitly.

## 8. Concurrency caveat (documented, not enforced)

Two teammates running agents with the **same** `ORACLE_KEYS` against the shared contract
simultaneously will collide on nonces (`Known transaction`). The demo convention is
**one person runs at a time**; `up` prints a one-line reminder. Per-member oracle
identities (true multi-operator DON) is out of scope / future work.

## 9. Health gates (ordering correctness, automated)

`up` blocks between stages on `wait_for` (bounded retry, clear timeout error), replacing
"wait 1–2 minutes and eyeball it":
1. chain: `eth_blockNumber` increases across two polls (block production), via tunnel.
2. TEE: `GET /tee_address` returns 200 with an address.
3. decryption nodes: each `GET /docs` returns 200.
Only then start oracle agents. A failed gate aborts `up` with the failing check + the
relevant `.run/*.log` path.

## 10. Acceptance

- `make doctor` on the current provisioned repo: all green, **zero mutations**.
- `make bootstrap` on the current provisioned repo: prints "already provisioned", **zero
  mutations** (verified: `.env` unchanged, no new tx, `git diff` clean).
- `make up` then `make demo` reproduces a `finalized=True` result against the shared chain;
  `make down` leaves no tracked process alive and does not touch shared instances.
- `python -m pytest tests/ -q` stays green and adds the new helper tests.
- A fresh teammate path (`sync` → `up` → `demo`) needs no manual command outside `make`
  beyond the §3 documented prerequisites.
```
