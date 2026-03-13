"""
MCP Gateway

Serves one FastMCP server per configured API, each mounted at /<name>/mcp.

Auth types:
  none               - no credentials added to backend requests
  bearer_passthrough - forwards the caller's Bearer token to the backend API

Config: config.yaml
Usage:  python gateway.py [--config config.yaml] [--host 0.0.0.0] [--port 8000]
"""

import argparse
import asyncio
import contextlib
import contextvars
import os
from typing import Any

import httpx
import uvicorn
import yaml
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

try:
    from fastmcp.server.providers.openapi import MCPType, RouteMap
except ImportError:
    from fastmcp.server.openapi import MCPType, RouteMap  # type: ignore[no-redef]

ROUTE_MAPS = [RouteMap(methods="*", mcp_type=MCPType.TOOL)]

# Per-request context var — set by bearer_passthrough middleware
_bearer_token: contextvars.ContextVar[str] = contextvars.ContextVar("bearer_token", default="")


def _check_json_response(response: httpx.Response) -> None:
    """Raise a clear error if the backend returned non-JSON (e.g. an HTML frontend page)."""
    content_type = response.headers.get("content-type", "")
    if response.is_success and "application/json" not in content_type:
        raise httpx.HTTPStatusError(
            f"Expected JSON response but got content-type {content_type!r}. "
            "The backend URL may be wrong or the endpoint does not exist.",
            request=response.request,
            response=response,
        )


class TokenPropagatingClient(httpx.AsyncClient):
    """Injects the per-request bearer token into every outbound API call."""

    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        token = _bearer_token.get()
        print(f"[DEBUG] TokenPropagatingClient.send: token={token[:20]!r}... present={bool(token)}")
        if token:
            request.headers["Authorization"] = f"Bearer {token}"
        response = await super().send(request, **kwargs)
        _check_json_response(response)
        return response


class BearerPassthroughMiddleware:
    """Raw ASGI middleware — extracts Bearer token from incoming request and sets context var."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            token = auth[7:] if auth.lower().startswith("bearer ") else auth
            print(f"[DEBUG] BearerPassthrough: auth_header={auth[:20]!r}... "
                  f"token_present={bool(token)}")
            ctx = _bearer_token.set(token)
            try:
                await self.app(scope, receive, send)
            finally:
                _bearer_token.reset(ctx)
        else:
            await self.app(scope, receive, send)


async def load_api(name: str, cfg: dict) -> Any:
    """Fetch the OpenAPI spec and build the ASGI app for one API entry."""
    spec_url = cfg["spec"]
    base_url = cfg.get("base_url", "")
    auth = cfg.get("auth", "none")

    print(f"  Loading {name} ({auth}) from {spec_url}")
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        resp = await client.get(spec_url)
        resp.raise_for_status()
        spec = resp.json()

    if auth == "bearer_passthrough":
        api_client: httpx.AsyncClient = TokenPropagatingClient(
            base_url=base_url,
            headers={"Accept": "application/json"},
            verify=False,
            timeout=60.0,
        )
    else:  # none
        class _ValidatingClient(httpx.AsyncClient):
            async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
                response = await super().send(request, **kwargs)
                _check_json_response(response)
                return response

        api_client = _ValidatingClient(
            base_url=base_url,
            headers={"Accept": "application/json"},
            verify=False,
            timeout=60.0,
        )

    mcp = FastMCP.from_openapi(
        openapi_spec=spec,
        client=api_client,
        route_maps=ROUTE_MAPS,
        name=name,
    )

    mcp_http_app = mcp.http_app(transport="streamable-http")

    if auth == "bearer_passthrough":
        mount_app = BearerPassthroughMiddleware(mcp_http_app)
    else:
        mount_app = mcp_http_app

    return mcp_http_app, mount_app


async def build_gateway(config_path: str) -> Starlette:
    with open(config_path) as f:
        config = yaml.safe_load(f)

    apis = config.get("apis", {})
    if not apis:
        raise ValueError(f"No apis defined in {config_path}")

    print(f"Loading {len(apis)} API(s)...")
    mcp_http_apps = []
    routes = []
    for name, cfg in apis.items():
        mcp_http_app, mount_app = await load_api(name, cfg)
        mcp_http_apps.append(mcp_http_app)
        routes.append(Mount(f"/{name}", app=mount_app))

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "apis": list(apis.keys())})

    async def well_known_mcp(request: Request) -> JSONResponse:
        base = str(request.base_url).rstrip("/")
        return JSONResponse({
            "mcp_version": "2024-11-05",
            "servers": [
                {
                    "name": name,
                    "url": f"{base}/{name}/mcp",
                    "transport": "streamable-http",
                }
                for name in apis
            ],
        })

    async def debug_headers(request: Request) -> JSONResponse:
        headers = dict(request.headers)
        token = _bearer_token.get()
        return JSONResponse({
            "headers": headers,
            "bearer_token_in_ctx": bool(token),
            "token_prefix": token[:12] + "..." if token else None,
        })

    routes.append(Route("/healthz", healthz))
    routes.append(Route("/.well-known/mcp.json", well_known_mcp))
    routes.append(Route("/debug/headers", debug_headers))

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> Any:
        async with contextlib.AsyncExitStack() as stack:
            for mcp_http_app in mcp_http_apps:
                await stack.enter_async_context(mcp_http_app.lifespan(app))
            yield

    return Starlette(routes=routes, lifespan=lifespan)


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP Gateway")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8001")))
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    args = parser.parse_args()

    app = asyncio.run(build_gateway(args.config))
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
