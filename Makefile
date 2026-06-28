# RMBS Confidential Compute — startup automation.
# Teammate flow:  make sync -> infra-up -> up -> demo -> down -> infra-down
# Owner setup (one-time):  make tee-install -> bootstrap -> publish-config
.DEFAULT_GOAL := help
SHELL := /bin/bash

IAF ?= 500000
PAF ?= 1000000

.PHONY: help doctor sync up down status demo result infra-up infra-down bootstrap publish-config tee-install tee-deploy tee-restart tee-logs

help: ## show this help
	@echo "Teammate:  make sync | infra-up | up | demo | down | infra-down | status | doctor"
	@echo "Owner:     make tee-install | bootstrap | publish-config | tee-deploy | tee-restart | tee-logs"
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

demo: ## submit a request, wait, print + archive the result to demo-results/ (override IAF=/PAF=)
	@source .venv/bin/activate && set -a && source .env && set +a && \
	  ID=$$(python submit_request.py --iaf $(IAF) --paf $(PAF) | tee /dev/stderr \
	        | grep -oE 'id=[0-9]+' | head -1 | cut -d= -f2) && \
	  { [ -n "$$ID" ] || { echo "make demo: could not extract request id — see submit output above"; exit 1; }; } && \
	  python demo_record.py $$ID --iaf $(IAF) --paf $(PAF)

result: ## read a finalized result on-chain by id: make result ID=10
	@[ -n "$(ID)" ] || { echo "usage: make result ID=<request-id>"; exit 1; }
	@source .venv/bin/activate && python read_result.py $(ID)

infra-up: ## start the shared cloud instances (TEE auto-starts on boot; needs start/stop + IAP perms)
	@bash ops/infra_up.sh

infra-down: ## stop the shared cloud instances (cost control)
	@bash ops/infra_down.sh

bootstrap: ## owner: idempotent ensure-provisioned (no-op when already done)
	@bash ops/bootstrap.sh

publish-config: ## owner: push config bundle to the shared tee-node
	@bash ops/publish_config.sh

tee-install: ## owner: install+enable the rmbs-tee systemd service on tee-node (one-time)
	@bash ops/tee_install.sh

tee-deploy: ## owner: push updated TEE .py code to tee-node + restart (never touches tee/kd/)
	@bash ops/tee_deploy.sh

tee-restart: ## owner: restart the rmbs-tee service
	@bash ops/tee_restart.sh

tee-logs: ## owner: tail the rmbs-tee service logs
	@bash ops/tee_logs.sh
