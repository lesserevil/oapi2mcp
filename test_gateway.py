"""Tests for gateway.py."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from gateway import (
    BearerPassthroughMiddleware,
    TokenPropagatingClient,
    _bearer_token,
    _check_json_response,
    build_gateway,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int, content_type: str) -> httpx.Response:
    request = httpx.Request("GET", "http://example.com/path")
    return httpx.Response(
        status_code,
        headers={"content-type": content_type},
        text="body",
        request=request,
    )


class _MockTransport(httpx.AsyncBaseTransport):
    """Returns a fixed response for every request."""

    def __init__(self, status_code: int, content_type: str, body: str = "body") -> None:
        self._status_code = status_code
        self._content_type = content_type
        self._body = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            self._status_code,
            headers={"content-type": self._content_type},
            text=self._body,
            request=request,
        )


# ---------------------------------------------------------------------------
# _check_json_response
# ---------------------------------------------------------------------------

def test_check_json_passes_for_json_content_type():
    _check_json_response(_make_response(200, "application/json"))


def test_check_json_passes_for_json_with_charset():
    _check_json_response(_make_response(200, "application/json; charset=utf-8"))


def test_check_json_raises_for_html():
    with pytest.raises(httpx.HTTPStatusError, match="Expected JSON response"):
        _check_json_response(_make_response(200, "text/html; charset=utf-8"))


def test_check_json_raises_for_missing_content_type():
    with pytest.raises(httpx.HTTPStatusError, match="Expected JSON response"):
        _check_json_response(_make_response(200, ""))


def test_check_json_ignores_error_responses():
    # 4xx/5xx already have meaningful status codes — don't double-raise
    _check_json_response(_make_response(401, "text/html"))
    _check_json_response(_make_response(404, "text/html"))
    _check_json_response(_make_response(500, "text/html"))


# ---------------------------------------------------------------------------
# TokenPropagatingClient — send()
#
# fastmcp builds requests externally via RequestDirector and calls
# client.send(request) directly — it never calls client.build_request().
# Tests must exercise the send() path with a pre-built request to match
# how the client is actually used in production.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_injects_bearer_token_into_prebuilt_request():
    """Token from context var is injected when fastmcp calls send() with a pre-built request."""
    captured: dict = {}

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200, headers={"content-type": "application/json"}, text="{}", request=request
            )

    client = TokenPropagatingClient(base_url="http://example.com", transport=CapturingTransport())
    request = httpx.Request("GET", "http://example.com/api/v1/foo")  # pre-built, no auth header

    ctx = _bearer_token.set("secret-token")
    try:
        await client.send(request)
    finally:
        _bearer_token.reset(ctx)

    assert captured["auth"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_send_no_token_omits_auth_header():
    """No Authorization header is added when context var is empty."""
    captured: dict = {}

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200, headers={"content-type": "application/json"}, text="{}", request=request
            )

    client = TokenPropagatingClient(base_url="http://example.com", transport=CapturingTransport())
    request = httpx.Request("GET", "http://example.com/api/v1/foo")

    ctx = _bearer_token.set("")
    try:
        await client.send(request)
    finally:
        _bearer_token.reset(ctx)

    assert captured["auth"] is None


@pytest.mark.asyncio
async def test_send_raises_on_html_response():
    client = TokenPropagatingClient(
        base_url="http://example.com",
        transport=_MockTransport(200, "text/html", "<html></html>"),
    )
    with pytest.raises(httpx.HTTPStatusError, match="Expected JSON response"):
        await client.send(httpx.Request("GET", "http://example.com/health"))


@pytest.mark.asyncio
async def test_send_passes_json_response():
    client = TokenPropagatingClient(
        base_url="http://example.com",
        transport=_MockTransport(200, "application/json", '{"ok": true}'),
    )
    resp = await client.send(httpx.Request("GET", "http://example.com/api/status"))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_send_passes_error_response():
    # A 404 with HTML body should not raise — let the caller handle HTTP errors
    client = TokenPropagatingClient(
        base_url="http://example.com",
        transport=_MockTransport(404, "text/html", "<html>not found</html>"),
    )
    resp = await client.send(httpx.Request("GET", "http://example.com/missing"))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# BearerPassthroughMiddleware
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_extracts_bearer_token():
    captured: dict = {}

    async def inner_app(scope, receive, send):
        captured["token"] = _bearer_token.get()

    middleware = BearerPassthroughMiddleware(inner_app)
    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer mytoken123")],
    }
    await middleware(scope, None, None)
    assert captured["token"] == "mytoken123"


@pytest.mark.asyncio
async def test_middleware_no_auth_header():
    captured: dict = {}

    async def inner_app(scope, receive, send):
        captured["token"] = _bearer_token.get()

    middleware = BearerPassthroughMiddleware(inner_app)
    scope = {"type": "http", "headers": []}
    await middleware(scope, None, None)
    assert captured["token"] == ""


@pytest.mark.asyncio
async def test_middleware_bearer_case_insensitive():
    captured: dict = {}

    async def inner_app(scope, receive, send):
        captured["token"] = _bearer_token.get()

    middleware = BearerPassthroughMiddleware(inner_app)
    scope = {
        "type": "http",
        "headers": [(b"authorization", b"BEARER uppercasetoken")],
    }
    await middleware(scope, None, None)
    assert captured["token"] == "uppercasetoken"


@pytest.mark.asyncio
async def test_middleware_resets_token_after_request():
    ctx = _bearer_token.set("")
    try:
        async def inner_app(scope, receive, send):
            pass

        middleware = BearerPassthroughMiddleware(inner_app)
        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer leaked")],
        }
        await middleware(scope, None, None)
        assert _bearer_token.get() == ""
    finally:
        _bearer_token.reset(ctx)


@pytest.mark.asyncio
async def test_middleware_passes_through_non_http_scope():
    called: list = []

    async def inner_app(scope, receive, send):
        called.append(scope["type"])

    middleware = BearerPassthroughMiddleware(inner_app)
    await middleware({"type": "lifespan"}, None, None)
    assert called == ["lifespan"]


# ---------------------------------------------------------------------------
# build_gateway
# ---------------------------------------------------------------------------

def _mock_mcp_http_app() -> MagicMock:
    app = MagicMock()

    @asynccontextmanager
    async def mock_lifespan(_app):
        yield

    app.lifespan = mock_lifespan
    return app


@pytest.mark.asyncio
async def test_build_gateway_raises_on_empty_apis(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("apis: {}\n")
    with pytest.raises(ValueError, match="No apis defined"):
        await build_gateway(str(config))


@pytest.mark.asyncio
async def test_build_gateway_healthz(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "apis:\n"
        "  myapi:\n"
        "    spec: http://mock.local/spec.json\n"
        "    base_url: http://mock.local\n"
        "    auth: none\n"
    )
    mock_app = _mock_mcp_http_app()
    with patch("gateway.load_api", new=AsyncMock(return_value=(mock_app, mock_app))):
        app = await build_gateway(str(config))

    with TestClient(app) as client:
        resp = client.get("/healthz")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["apis"] == ["myapi"]


@pytest.mark.asyncio
async def test_build_gateway_mounts_api_at_correct_path(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "apis:\n"
        "  testapi:\n"
        "    spec: http://mock.local/spec.json\n"
        "    base_url: http://mock.local\n"
        "    auth: none\n"
    )
    mock_app = _mock_mcp_http_app()
    with patch("gateway.load_api", new=AsyncMock(return_value=(mock_app, mock_app))):
        app = await build_gateway(str(config))

    route_paths = [r.path for r in app.routes]
    assert "/testapi" in route_paths


@pytest.mark.asyncio
async def test_build_gateway_calls_load_api_with_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "apis:\n"
        "  myapi:\n"
        "    spec: http://mock.local/spec.json\n"
        "    base_url: http://mock.local\n"
        "    auth: bearer_passthrough\n"
    )
    mock_app = _mock_mcp_http_app()
    with patch("gateway.load_api", new=AsyncMock(return_value=(mock_app, mock_app))) as mock_load:
        await build_gateway(str(config))

    mock_load.assert_called_once_with(
        "myapi",
        {
            "spec": "http://mock.local/spec.json",
            "base_url": "http://mock.local",
            "auth": "bearer_passthrough",
        },
    )


@pytest.mark.asyncio
async def test_well_known_mcp_structure(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "apis:\n"
        "  myapi:\n"
        "    spec: http://mock.local/spec.json\n"
        "    base_url: http://mock.local\n"
        "    auth: none\n"
    )
    mock_app = _mock_mcp_http_app()
    with patch("gateway.load_api", new=AsyncMock(return_value=(mock_app, mock_app))):
        app = await build_gateway(str(config))

    with TestClient(app) as client:
        resp = client.get("/.well-known/mcp.json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["mcp_version"] == "2024-11-05"
    assert len(data["servers"]) == 1
    server = data["servers"][0]
    assert server["name"] == "myapi"
    assert server["url"].endswith("/myapi/mcp")
    assert server["transport"] == "streamable-http"


@pytest.mark.asyncio
async def test_well_known_mcp_url_uses_request_host(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "apis:\n"
        "  myapi:\n"
        "    spec: http://mock.local/spec.json\n"
        "    base_url: http://mock.local\n"
        "    auth: none\n"
    )
    mock_app = _mock_mcp_http_app()
    with patch("gateway.load_api", new=AsyncMock(return_value=(mock_app, mock_app))):
        app = await build_gateway(str(config))

    with TestClient(app, base_url="http://gateway.example.com") as client:
        resp = client.get("/.well-known/mcp.json")

    server = resp.json()["servers"][0]
    assert server["url"] == "http://gateway.example.com/myapi/mcp"


@pytest.mark.asyncio
async def test_well_known_mcp_lists_all_apis(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "apis:\n"
        "  api1:\n"
        "    spec: http://mock1.local/spec.json\n"
        "    base_url: http://mock1.local\n"
        "    auth: none\n"
        "  api2:\n"
        "    spec: http://mock2.local/spec.json\n"
        "    base_url: http://mock2.local\n"
        "    auth: bearer_passthrough\n"
    )
    mock_app = _mock_mcp_http_app()
    with patch("gateway.load_api", new=AsyncMock(return_value=(mock_app, mock_app))):
        app = await build_gateway(str(config))

    with TestClient(app) as client:
        resp = client.get("/.well-known/mcp.json")

    servers = {s["name"]: s for s in resp.json()["servers"]}
    assert set(servers) == {"api1", "api2"}
    assert servers["api1"]["url"].endswith("/api1/mcp")
    assert servers["api2"]["url"].endswith("/api2/mcp")


@pytest.mark.asyncio
async def test_build_gateway_multiple_apis(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        "apis:\n"
        "  api1:\n"
        "    spec: http://mock1.local/spec.json\n"
        "    base_url: http://mock1.local\n"
        "    auth: none\n"
        "  api2:\n"
        "    spec: http://mock2.local/spec.json\n"
        "    base_url: http://mock2.local\n"
        "    auth: bearer_passthrough\n"
    )
    mock_app = _mock_mcp_http_app()
    with patch("gateway.load_api", new=AsyncMock(return_value=(mock_app, mock_app))):
        app = await build_gateway(str(config))

    with TestClient(app) as client:
        resp = client.get("/healthz")

    data = resp.json()
    assert set(data["apis"]) == {"api1", "api2"}
