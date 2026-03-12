.DEFAULT_GOAL := help

HORDE_DIR := mcp-servers/horde
HORDE_PYTHON := $(HORDE_DIR)/.venv/bin/python

.PHONY: help setup-horde run-horde stop-horde logs-horde run-horde-docker

help:
	@echo "MCP-as-a-Service"
	@echo ""
	@echo "  setup-horde       Install Python deps for Horde MCP"
	@echo "  run-horde         Start the Horde MCP server (SSE on :8001)"
	@echo "  run-horde-docker  Start via Docker Compose"
	@echo "  stop-horde        Stop Docker Compose service"
	@echo "  logs-horde        Tail Docker Compose logs"
	@echo ""
	@echo "Setup: cp $(HORDE_DIR)/.env.example $(HORDE_DIR)/.env && edit AUTH_TOKEN"

setup-horde:
	cd $(HORDE_DIR) && uv venv --python 3.12 .venv 2>/dev/null || true
	cd $(HORDE_DIR) && uv pip install fastmcp httpx uvicorn

run-horde:
	@if [ ! -f $(HORDE_DIR)/.env ]; then \
		echo "Missing $(HORDE_DIR)/.env — copy from .env.example and set AUTH_TOKEN"; \
		exit 1; \
	fi
	@if [ ! -f $(HORDE_PYTHON) ]; then \
		echo "Missing venv — run: make setup-horde"; \
		exit 1; \
	fi
	cd $(HORDE_DIR) && set -a && . ./.env && set +a && \
		$(abspath $(HORDE_PYTHON)) server.py

run-horde-docker:
	docker compose -f $(HORDE_DIR)/docker-compose.yml up -d

stop-horde:
	docker compose -f $(HORDE_DIR)/docker-compose.yml down

logs-horde:
	docker compose -f $(HORDE_DIR)/docker-compose.yml logs -f
