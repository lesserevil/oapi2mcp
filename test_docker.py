"""
Docker integration tests for the oapi2mcp container.

These tests build the image once per session and exercise:
  - Image structure (gateway.py present, config.yaml absent)
  - Entrypoint and exposed port
  - Container fails without a config file
  - Container starts, serves /healthz, when a valid config is mounted

Requires Docker to be available. Skipped automatically otherwise.
Uses --network=host so the container can reach a mock HTTP server in the
test process (this works on Linux; on macOS host.docker.internal is used
instead, but these tests are expected to run on Linux in CI).
"""

import json
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest

IMAGE_NAME = "oapi2mcp-test"
PROJECT_ROOT = Path(__file__).parent

MINIMAL_OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _stop_container(container_id: str) -> None:
    subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docker_image():
    """Build the Docker image once for the entire test session."""
    result = _run(
        ["docker", "build", "-t", IMAGE_NAME, "."],
        cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, (
        f"docker build failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    yield IMAGE_NAME
    subprocess.run(["docker", "rmi", "-f", IMAGE_NAME], capture_output=True)


@pytest.fixture()
def running_container():
    """
    Context for tests that need a running container.
    Yields a list; append a container ID to have it cleaned up automatically.
    """
    containers: list[str] = []
    yield containers
    for cid in containers:
        _stop_container(cid)


# ---------------------------------------------------------------------------
# Skip marker
# ---------------------------------------------------------------------------

requires_docker = pytest.mark.skipif(
    not _docker_available(), reason="Docker not available"
)


# ---------------------------------------------------------------------------
# Image structure tests  (docker_image fixture ensures the build passed)
# ---------------------------------------------------------------------------


@requires_docker
def test_image_builds(docker_image):
    """Passes if the session fixture built the image without error."""


@requires_docker
def test_gateway_py_is_in_image(docker_image):
    result = _run(["docker", "run", "--rm", docker_image, "test", "-f", "/app/gateway.py"])
    assert result.returncode == 0, "gateway.py should be present in the image"


@requires_docker
def test_config_yaml_not_in_image(docker_image):
    """config.yaml must never be baked into the image."""
    result = _run(["docker", "run", "--rm", docker_image, "test", "-f", "/app/config.yaml"])
    assert result.returncode != 0, "config.yaml must NOT be present in the image"


@requires_docker
def test_entrypoint_runs_gateway(docker_image):
    """Image entrypoint should be 'python gateway.py'."""
    result = _run(["docker", "inspect", "--format={{ json .Config.Entrypoint }}", docker_image])
    assert result.returncode == 0
    entrypoint = json.loads(result.stdout.strip())
    assert entrypoint == ["python", "gateway.py"]


@requires_docker
def test_port_8000_is_exposed(docker_image):
    result = _run(["docker", "inspect", "--format={{ json .Config.ExposedPorts }}", docker_image])
    assert result.returncode == 0
    ports = json.loads(result.stdout.strip())
    assert "8000/tcp" in ports


# ---------------------------------------------------------------------------
# Runtime — failure without config
# ---------------------------------------------------------------------------


@requires_docker
def test_container_fails_without_config(docker_image):
    """Container must exit non-zero when no config file is mounted."""
    result = _run(
        ["docker", "run", "--rm", "--network=host", docker_image],
        timeout=15,
    )
    assert result.returncode != 0, (
        "Container should exit non-zero when config.yaml is absent"
    )


# ---------------------------------------------------------------------------
# Runtime — end-to-end with mock spec server
# ---------------------------------------------------------------------------


class _SpecHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that serves MINIMAL_OPENAPI at /spec.json."""

    def do_GET(self):  # noqa: N802
        if self.path == "/spec.json":
            body = json.dumps(MINIMAL_OPENAPI).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # suppress server noise in test output


@pytest.fixture()
def spec_server():
    """Start a local HTTP server that serves the minimal OpenAPI spec."""
    port = _free_port()
    server = HTTPServer(("0.0.0.0", port), _SpecHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


@requires_docker
def test_gateway_healthz_with_mounted_config(
    docker_image, spec_server, running_container, tmp_path
):
    """
    Full end-to-end: container starts with a mounted config pointing at a local
    mock spec server and responds 200 on /healthz.
    """
    spec_port = spec_server
    gw_port = _free_port()

    config = tmp_path / "config.yaml"
    config.write_text(
        f"apis:\n"
        f"  testapi:\n"
        f"    spec: http://localhost:{spec_port}/spec.json\n"
        f"    base_url: http://localhost:{spec_port}\n"
        f"    auth: none\n"
    )

    result = _run([
        "docker", "run", "-d",
        "--network=host",
        "-v", f"{config}:/app/config.yaml:ro",
        docker_image,
        "--port", str(gw_port),
    ])
    assert result.returncode == 0, f"docker run failed:\n{result.stderr}"
    container_id = result.stdout.strip()
    running_container.append(container_id)

    # Poll until /healthz responds or timeout
    deadline = time.monotonic() + 30
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"http://localhost:{gw_port}/healthz", timeout=2)
            break
        except Exception as exc:
            last_exc = exc
            time.sleep(0.5)
    else:
        logs = _run(["docker", "logs", container_id]).stdout
        pytest.fail(f"Gateway never became healthy. Last error: {last_exc}\nLogs:\n{logs}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["apis"] == ["testapi"]
