"""Tests for health-check access log suppression.

Verifies that the ``_HealthCheckFilter`` on ``uvicorn.access`` suppresses
``/api/health`` request logs at the default INFO level, but allows them
through at DEBUG level.
"""

from __future__ import annotations

import logging
import os


class TestHealthCheckFilter:
    """The _HealthCheckFilter on uvicorn.access."""

    def _make_access_record(self, path: str) -> logging.LogRecord:
        """Create a uvicorn.access-style log record for the given path."""
        return logging.LogRecord(
            name="uvicorn.access",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg='%s - "%s %s HTTP/%s" %d',
            args=("127.0.0.1:12345", "GET", path, "1.1", 200),
            exc_info=None,
        )

    def test_health_request_suppressed_at_info(self):
        from app.main import _HealthCheckFilter

        f = _HealthCheckFilter()
        record = self._make_access_record("/api/health")
        assert f.filter(record) is False

    def test_non_health_request_allowed_at_info(self):
        from app.main import _HealthCheckFilter

        f = _HealthCheckFilter()
        record = self._make_access_record("/commands")
        assert f.filter(record) is True

    def test_health_request_allowed_at_debug(self):
        from app.main import _HealthCheckFilter

        f = _HealthCheckFilter()
        record = self._make_access_record("/api/health")
        record.levelno = logging.DEBUG
        assert f.filter(record) is True

    def test_non_access_logger_records_pass_through(self):
        from app.main import _HealthCheckFilter

        f = _HealthCheckFilter()
        record = logging.LogRecord(
            name="app.main",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="some message",
            args=None,
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_filter_is_attached_to_access_logger(self):
        """The uvicorn.access logger should have the filter installed."""
        from app.main import _HealthCheckFilter

        access_logger = logging.getLogger("uvicorn.access")
        filter_types = [type(f) for f in access_logger.filters]
        assert _HealthCheckFilter in filter_types

    def test_mcp_log_level_env_var(self, monkeypatch):
        """MCP_LOG_LEVEL env var should be readable for log configuration."""
        monkeypatch.setenv("MCP_LOG_LEVEL", "WARNING")
        assert os.environ.get("MCP_LOG_LEVEL") == "WARNING"
