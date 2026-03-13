# oapi2mcp — Multi-Service OpenAPI → MCP Gateway

## Goal

A single deployable container (or K8s pod) that accepts one YAML config file and
exposes multiple MCP endpoints — one per configured upstream OpenAPI service — all
behind a single HTTP server.

```
https://gateway/foo/mcp   →  MCP tools generated from foo's OpenAPI spec
https://gateway/bar/mcp   →  MCP tools generated from bar's OpenAPI spec
https://gateway/healthz   →  liveness probe
```

Each MCP endpoint is independently auth-aware. Clients supply their own bearer
tokens; the gateway forwards them to the upstream API transparently.

---

## Architecture

```
                    ┌─────────────────────────────────────────────┐
MCP Client A ──────▶│  POST /foo/mcp   BearerPassthroughMiddleware│
  (token=T_A)       │                         │                   │
                    │            ┌────────────▼──────────────┐    │
MCP Client B ──────▶│  POST /bar/mcp   FastMCP (from_openapi) │    │
  (token=T_B)       │                         │                   │
                    │     TokenPropagatingClient (httpx)          │
                    └──────────────────┬──────────────────────────┘
                                       │
                         ┌─────────────┴──────────────┐
                         ▼                             ▼
                   upstream foo API           upstream bar API
                   Authorization: Bearer T_A  Authorization: Bearer T_B
```

**Key design decisions:**

- One `FastMCP` instance per API, each built with `FastMCP.from_openapi()`.
- Token isolation via `contextvars` — each concurrent request carries its own token,
  no cross-contamination between simultaneous callers.
- All `FastMCP` ASGI apps are mounted into a single `Starlette` router at startup,
  keeping the process count at 1.
- Auth is per-API — `none` or `bearer_passthrough` — so mixed deployments work fine.

---

## Config File Format

```yaml
# config.yaml
apis:
  foo:                              # becomes the URL slug: /foo/mcp
    spec: https://api.foo.com/openapi.json
    base_url: https://api.foo.com
    auth: bearer_passthrough        # forward caller's Bearer token

  bar:
    spec: ./specs/bar.json          # local file also supported (planned)
    base_url: https://api.bar.com
    auth: none                      # unauthenticated upstream

  internal:
    spec: http://internal-svc/openapi.json
    base_url: http://internal-svc
    auth: bearer_passthrough
    tls_verify: false               # skip TLS for internal services
```

### Auth values

| Value | Behaviour |
|-------|-----------|
| `none` | No `Authorization` header added to upstream requests |
| `bearer_passthrough` | Copies caller's `Authorization: Bearer <token>` to the upstream request |

Future auth types (out of scope for v1):

| Value | Behaviour |
|-------|-----------|
| `bearer_static` | Uses a fixed token from an env var or secret; no client token needed |
| `basic` | Basic auth credentials from config/env |
| `api_key_header` | Puts a static key into a named header |

---

## Current State (`gateway.py`)

Already implemented:

- [x] Parses `config.yaml`
- [x] Fetches OpenAPI spec from a URL at startup
- [x] Builds a `FastMCP` tool server per API via `from_openapi()`
- [x] Mounts all MCP apps under `/<name>/mcp` in a single Starlette app
- [x] `bearer_passthrough` via `BearerPassthroughMiddleware` + `TokenPropagatingClient`
  - Token injected in `send()`, not `build_request()` — fastmcp calls `send()` directly with pre-built requests
- [x] `none` auth (validating `httpx.AsyncClient` subclass)
- [x] `/healthz` endpoint listing loaded APIs
- [x] `/debug/headers` endpoint showing bearer token context state
- [x] CLI flags: `--config`, `--host`, `--port`, `--log-level`
- [x] Env var overrides: `HOST`, `PORT`, `LOG_LEVEL`
- [x] `pyproject.toml` with pytest and ruff config
- [x] `test_gateway.py` — unit tests for all gateway components
- [x] GitHub Actions CI — lint + test on PRs and pushes to main

---

## Gaps & Remaining Work

### P0 — Required for usable deployment

| # | Item | Notes |
|---|------|-------|
| 1 | **Dockerfile** | Multi-stage: builder installs deps with `uv`, final image runs `gateway.py` |
| 2 | ~~**`pyproject.toml` / `requirements.txt`**~~ | Done — `pyproject.toml` with ruff + pytest config |
| 3 | **Local spec file support** | `spec: ./path/to/spec.json` — detect file:// or relative paths and read from disk instead of HTTP |
| 4 | **Startup failure isolation** | If one API's spec fetch fails, log and skip it rather than crashing the whole gateway |
| 5 | **Config validation** | Fail fast with clear error messages for missing required fields |

### P1 — Important for production

| # | Item | Notes |
|---|------|-------|
| 6 | **K8s manifests** | `Deployment`, `Service`, `ConfigMap` (for config.yaml), optional `Ingress` |
| 7 | **Helm chart** or Kustomize overlay | Parameterise image tag, replicas, resource limits |
| 8 | **Graceful shutdown** | Drain in-flight requests on SIGTERM; uvicorn already supports this |
| 9 | **Structured logging** | JSON log lines with `api`, `method`, `path`, `duration_ms`, `status` for each upstream call |
| 10 | **Liveness vs readiness probes** | `/healthz` = liveness. `/readyz` = readiness (only 200 once all specs loaded) |
| 11 | **Route filtering** | Config option to include/exclude specific OpenAPI operations by tag, path, or method |

### P2 — Nice to have

| # | Item | Notes |
|---|------|-------|
| 12 | **`bearer_static` auth** | Read token from env var (e.g. `FOO_API_TOKEN`) so the gateway itself authenticates |
| 13 | **Spec caching + refresh** | Cache specs on disk; reload on SIGHUP or on a TTL |
| 14 | **Admin endpoint** | `GET /admin/apis` — shows loaded APIs, spec source, tool count, last refresh time |
| 15 | **Metrics** | Prometheus `/metrics` — request counts, latency histograms per API |
| 16 | **mTLS** | For gateways fronting internal services that require client certs |

---

## File Layout (target)

```
oapi2mcp/
├── gateway.py              # main entrypoint ✓
├── config.yaml             # example config ✓
├── pyproject.toml          # deps + build metadata ✓
├── Makefile                # dev helpers ✓
├── test_gateway.py         # unit tests ✓
├── .github/workflows/
│   └── test.yml            # CI: lint + test on PR / main ✓
├── Dockerfile              # container image                [TODO]
├── k8s/
│   ├── deployment.yaml                                      [TODO]
│   ├── service.yaml                                         [TODO]
│   ├── configmap.yaml                                       [TODO]
│   └── ingress.yaml        # optional, TLS termination      [TODO]
├── helm/                   # optional Helm chart            [TODO]
│   ├── Chart.yaml
│   ├── values.yaml
│   └── templates/
├── PLAN.md                 # this file
└── fixtures/
    └── petstore.json       # local spec for testing         [TODO]
```

---

## Dockerfile (design)

```dockerfile
# --- builder ---
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml .
RUN pip install uv && uv pip install --system .

# --- runtime ---
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY gateway.py .

ENV HOST=0.0.0.0 PORT=8000 LOG_LEVEL=info
EXPOSE 8000

ENTRYPOINT ["python", "gateway.py"]
CMD ["--config", "/config/config.yaml"]
```

The config file is mounted into the container at `/config/config.yaml` — via a
Docker volume bind mount or a K8s `ConfigMap` volume.

**Docker run example:**
```bash
docker run -p 8000:8000 \
  -v $(pwd)/config.yaml:/config/config.yaml:ro \
  oapi2mcp:latest
```

---

## K8s Deployment (design)

Config is delivered as a `ConfigMap`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: oapi2mcp-config
data:
  config.yaml: |
    apis:
      foo:
        spec: https://api.foo.com/openapi.json
        base_url: https://api.foo.com
        auth: bearer_passthrough
```

Mounted into the pod:

```yaml
volumes:
  - name: config
    configMap:
      name: oapi2mcp-config
containers:
  - name: gateway
    image: oapi2mcp:latest
    args: ["--config", "/config/config.yaml"]
    volumeMounts:
      - name: config
        mountPath: /config
    ports:
      - containerPort: 8000
    livenessProbe:
      httpGet: { path: /healthz, port: 8000 }
    readinessProbe:
      httpGet: { path: /readyz, port: 8000 }
```

For bearer tokens in the `bearer_static` case (future), tokens are injected via K8s
`Secret` → environment variables, never baked into the ConfigMap.

---

## MCP Client Configuration

Each API gets its own MCP endpoint URL. Clients configure it like any other MCP
server using streamable-HTTP transport:

```json
{
  "mcpServers": {
    "foo": {
      "url": "https://gateway.example.com/foo/mcp",
      "headers": {
        "Authorization": "Bearer <client-token>"
      }
    },
    "bar": {
      "url": "https://gateway.example.com/bar/mcp",
      "headers": {
        "Authorization": "Bearer <different-client-token>"
      }
    }
  }
}
```

The gateway strips no headers — the `Authorization` header set by the MCP client is
forwarded verbatim to the upstream API for `bearer_passthrough` APIs.

---

## Testing Strategy

1. **Unit** — validate config parsing and auth middleware logic with a mock ASGI app.
2. **Integration** — spin up the gateway pointing at a local mock HTTP server (e.g.
   `respx` or a Starlette test app serving a petstore spec), make MCP tool calls,
   assert correct `Authorization` forwarding.
3. **Container smoke test** — `docker build && docker run`, hit `/healthz`, confirm
   `200 OK`.
4. **K8s smoke test** — `kubectl apply -f k8s/` in a local `kind` cluster, port-
   forward, hit `/healthz`.

---

## Open Questions

- **Spec reload** — should the gateway support live reload (SIGHUP or watch) without
  downtime? Likely yes for production, but not P0.
- **Multi-replica token isolation** — `contextvars` already makes per-request token
  isolation correct within a single process. Multiple replicas are fine since each
  request is fully self-contained.
- **Large specs** — OpenAPI specs for large services can be 10k+ operations. Should
  we support a `filter` key in config to include only specific tags or paths?
- **SSE vs streamable-HTTP** — `streamable-http` is the current default. Do any
  target MCP clients require SSE? If so, expose both transports or make it
  configurable.
