# oapi2mcp

[![Test](https://github.com/lesserevil/oapi2mcp/actions/workflows/test.yml/badge.svg)](https://github.com/lesserevil/oapi2mcp/actions/workflows/test.yml)

A single gateway that turns any OpenAPI spec into an MCP server. Configure one or more upstream APIs in `config.yaml` and the gateway exposes a separate MCP endpoint for each — all behind one HTTP server.

```
https://gateway/horde/mcp   →  MCP tools from horde's OpenAPI spec
https://gateway/foo/mcp     →  MCP tools from foo's OpenAPI spec
https://gateway/healthz     →  liveness probe
```

## Quick Start

```bash
make setup
make run
```

## Docker

```bash
# Build
make build
# or: docker build -t oapi2mcp .
# or: IMAGE_TAG=myrepo/oapi2mcp:latest make build

# Run — mount your config file at /app/config.yaml
docker run -p 8000:8000 \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  oapi2mcp
```

Override host/port via CLI args or environment variables:

```bash
# Custom port via arg
docker run -p 9000:9000 \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  oapi2mcp --port 9000

# Custom port via env var
docker run -p 9000:9000 \
  -e PORT=9000 \
  -v "$(pwd)/config.yaml:/app/config.yaml:ro" \
  oapi2mcp
```

The container expects the config to be mounted at `/app/config.yaml`. It will exit non-zero if the file is absent or the spec URLs are unreachable.

### Building from inside a container

`make build` and `make test-docker` use the Docker CLI, which works inside a container via Docker-out-of-Docker — mount the host socket:

```bash
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$(pwd):/workspace" -w /workspace \
  <your-ci-image> \
  make build
```

## Configuration

```yaml
# config.yaml
apis:
  horde:
    spec: https://horde.nvidia.com/openapi.json
    base_url: https://horde.nvidia.com
    auth: bearer_passthrough   # forward caller's Bearer token upstream

  internal:
    spec: http://internal-svc/openapi.json
    base_url: http://internal-svc
    auth: none                 # no auth required
```

### Auth modes

| Value | Behaviour |
|-------|-----------|
| `none` | No `Authorization` header added to upstream requests |
| `bearer_passthrough` | Copies the caller's `Authorization: Bearer <token>` to every upstream request |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8001` | Listen port |
| `LOG_LEVEL` | `info` | uvicorn log level |

## MCP Client Setup

Each API gets its own endpoint. Example `.mcp.json` for Claude Code:

```json
{
  "mcpServers": {
    "horde": {
      "type": "http",
      "url": "http://localhost:8001/horde/mcp",
      "headers": {
        "Authorization": "Bearer ${HORDE_API_TOKEN}"
      }
    }
  }
}
```

> **Note:** `bearer_passthrough` requires the MCP client to supply the token — the gateway forwards it verbatim. The env var is substituted by Claude Code at request time.

## How It Works

```
MCP Client (token=T) ──▶  POST /horde/mcp
                              │
                    BearerPassthroughMiddleware
                         extracts T into contextvars
                              │
                    FastMCP (from_openapi)
                         dispatches tool call
                              │
                    TokenPropagatingClient.send()
                         injects Authorization: Bearer T
                              │
                    upstream API ◀──────────────────
```

Token isolation is per-request via `contextvars` — concurrent calls from different clients never cross-contaminate.

> **Implementation note:** fastmcp builds HTTP requests externally via `RequestDirector` and calls `client.send(request)` directly, bypassing `build_request()`. Token injection must happen in `send()`, not `build_request()`.

## Development

```bash
make setup        # create .venv and install deps
make test         # run unit tests
make test-docker  # run Docker integration tests (requires Docker)
make lint         # run ruff
make run          # start gateway on :8001
```

## Endpoints

| Path | Description |
|------|-------------|
| `/<name>/mcp` | MCP streamable-HTTP endpoint for the named API |
| `/healthz` | Returns `{"status": "ok", "apis": [...]}` |
| `/debug/headers` | Shows incoming headers and whether the bearer token context var is set |
