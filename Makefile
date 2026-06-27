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
	  { [ -n "$$ID" ] || { echo "make demo: could not extract request id — see submit output above"; exit 1; }; } && \
	  echo "waiting for the DON to finalize id=$$ID (async attestation; polling up to ~90s)..." && \
	  for i in $$(seq 1 30); do \
	    python read_result.py $$ID | grep -q 'finalized=True' && break; \
	    sleep 3; \
	  done; \
	  python read_result.py $$ID

infra-up: ## owner: start shared instances + remote TEE
	@bash ops/infra_up.sh

infra-down: ## owner: stop shared instances
	@bash ops/infra_down.sh

bootstrap: ## owner: idempotent ensure-provisioned (no-op when already done)
	@bash ops/bootstrap.sh

publish-config: ## owner: push config bundle to the shared tee-node
	@bash ops/publish_config.sh
