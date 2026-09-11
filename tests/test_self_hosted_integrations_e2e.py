"""End-to-end tests for the vital-pulse / penny-track registry scripts.

Spawns a tiny local HTTP server that mimics the two REST APIs and runs
the scripts against it, verifying:
- the scripts call the right URL with the X-API-Key header,
- JSON output is pretty-printed and passes through,
- non-zero exit + clear error on API failure (e.g. 401).
"""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"

API_KEY = "test-read-only-key"


class _MockHandler(BaseHTTPRequestHandler):
    """Minimal mock for both APIs; logs requests for assertions."""

    requests: ClassVar[list[dict]] = []

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        type(self).requests.append(
            {"path": self.path, "api_key": self.headers.get("X-API-Key")}
        )

        if self.headers.get("X-API-Key") != API_KEY:
            self._reply(401, {"error": "Invalid API key"})
            return

        if self.path.startswith("/api/v1/logs"):
            self._reply(200, {"data": [{"id": 1, "heart_rate": 65}], "meta": {"total": 1}})
        elif self.path.startswith("/api/receipts"):
            self._reply(200, {"data": [{"id": 1, "amount": 45.50}], "meta": {"total": 1}})
        else:
            self._reply(404, {"error": "not found"})

    def log_message(self, fmt: str, *args) -> None:
        pass


@pytest.fixture()
def mock_server():
    """Start a mock API server on a random port."""
    _MockHandler.requests = []
    server = HTTPServer(("127.0.0.1", 0), _MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        _MockHandler.requests = []


class TestVitalReadingsE2E:
    def test_returns_pretty_json(self, mock_server: str) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "vital_readings.sh"), "2025-01-01", "2025-01-31"],
            capture_output=True, text=True, timeout=10, check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "VITAL_PULSE_URL": mock_server,
                "VITAL_PULSE_API_KEY": API_KEY,
            },
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["data"][0]["heart_rate"] == 65
        # Correct URL + auth header
        assert _MockHandler.requests[0]["path"] == "/api/v1/logs?from=2025-01-01&to=2025-01-31"
        assert _MockHandler.requests[0]["api_key"] == API_KEY

    def test_rejects_bad_api_key(self, mock_server: str) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "vital_readings.sh"), "2025-01-01", "2025-01-31"],
            capture_output=True, text=True, timeout=10, check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "VITAL_PULSE_URL": mock_server,
                "VITAL_PULSE_API_KEY": "wrong",
            },
        )
        assert result.returncode != 0
        assert "request to vital-pulse failed" in result.stderr


class TestHttpRouteE2E:
    """Full FastAPI route: POST /vital_readings with the tool field names."""

    def test_vital_readings_route_works(
        self, monkeypatch: pytest.MonkeyPatch, mock_server: str,
    ) -> None:
        from fastapi.testclient import TestClient

        from app.registry import load_registry
        monkeypatch.setenv("VITAL_PULSE_URL", mock_server)
        monkeypatch.setenv("VITAL_PULSE_API_KEY", API_KEY)
        monkeypatch.setenv("MCP_API_KEY", "mcp-secret")
        load_registry()

        try:
            from app.main import create_app
            with TestClient(create_app()) as client:
                resp = client.post(
                    "/vital_readings",
                    headers={"X-API-Key": "mcp-secret"},
                    json={"from_date": "2025-01-01", "to_date": "2025-01-31"},
                )
        finally:
            load_registry()

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert json.loads(data["stdout"])["data"][0]["heart_rate"] == 65

    def test_penny_transactions_route_works(
        self, monkeypatch: pytest.MonkeyPatch, mock_server: str,
    ) -> None:
        from fastapi.testclient import TestClient

        from app.registry import load_registry
        monkeypatch.setenv("PENNY_TRACK_URL", mock_server)
        monkeypatch.setenv("PENNY_TRACK_API_KEY", API_KEY)
        monkeypatch.setenv("MCP_API_KEY", "mcp-secret")
        load_registry()

        try:
            from app.main import create_app
            with TestClient(create_app()) as client:
                resp = client.post(
                    "/penny_transactions",
                    headers={"X-API-Key": "mcp-secret"},
                    json={"from_date": "2025-01-01", "to_date": "2025-01-31"},
                )
        finally:
            load_registry()

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert json.loads(data["stdout"])["data"][0]["amount"] == 45.50


class TestPennyTransactionsE2E:
    def test_returns_pretty_json(self, mock_server: str) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "penny_transactions.sh"), "2025-01-01", "2025-01-31"],
            capture_output=True, text=True, timeout=10, check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "PENNY_TRACK_URL": mock_server,
                "PENNY_TRACK_API_KEY": API_KEY,
            },
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["data"][0]["amount"] == 45.50
        assert _MockHandler.requests[0]["path"] == "/api/receipts?from=2025-01-01&to=2025-01-31&limit=100"
        assert _MockHandler.requests[0]["api_key"] == API_KEY

    def test_rejects_bad_api_key(self, mock_server: str) -> None:
        result = subprocess.run(
            [str(SCRIPTS / "penny_transactions.sh"), "2025-01-01", "2025-01-31"],
            capture_output=True, text=True, timeout=10, check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "PENNY_TRACK_URL": mock_server,
                "PENNY_TRACK_API_KEY": "wrong",
            },
        )
        assert result.returncode != 0
        assert "request to penny-track failed" in result.stderr
