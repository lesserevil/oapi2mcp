.DEFAULT_GOAL := help

PYTHON := .venv/bin/python

.PHONY: help setup run test lint

help:
	@echo "oapi2mcp — OpenAPI → MCP gateway"
	@echo ""
	@echo "  setup   Install dependencies into .venv"
	@echo "  run     Start the gateway (streamable-http on :8000)"
	@echo "  test    Run tests"
	@echo "  lint    Run ruff linter"
	@echo ""
	@echo "Config: config.yaml"
	@echo "Env:    HOST, PORT, LOG_LEVEL"

setup:
	uv venv --python 3.13 .venv 2>/dev/null || true
	uv pip install --python $(PYTHON) fastmcp httpx uvicorn pyyaml pytest pytest-asyncio ruff

run:
	@if [ ! -f $(PYTHON) ]; then \
		echo "Missing venv — run: make setup"; \
		exit 1; \
	fi
	$(PYTHON) gateway.py --port $${PORT:-8001}

test:
	.venv/bin/pytest test_gateway.py -v

lint:
	.venv/bin/ruff check gateway.py test_gateway.py
