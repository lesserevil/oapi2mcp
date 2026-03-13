.DEFAULT_GOAL := help

PYTHON := .venv/bin/python
IMAGE_TAG ?= oapi2mcp

.PHONY: help setup run test test-docker build lint

help:
	@echo "oapi2mcp — OpenAPI → MCP gateway"
	@echo ""
	@echo "  setup        Install dependencies into .venv"
	@echo "  run          Start the gateway (streamable-http on :8000)"
	@echo "  test         Run unit tests"
	@echo "  test-docker  Run Docker integration tests (requires Docker)"
	@echo "  build        Build the Docker image (IMAGE_TAG=oapi2mcp)"
	@echo "  lint         Run ruff linter"
	@echo ""
	@echo "Config: config.yaml"
	@echo "Env:    HOST, PORT, LOG_LEVEL"
	@echo ""
	@echo "Note: 'build' and 'test-docker' require Docker."
	@echo "      When running inside a container, mount the Docker socket:"
	@echo "      -v /var/run/docker.sock:/var/run/docker.sock"

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

build:
	docker build -t $(IMAGE_TAG) .

test-docker:
	.venv/bin/pytest test_docker.py -v

lint:
	.venv/bin/ruff check gateway.py test_gateway.py test_docker.py
