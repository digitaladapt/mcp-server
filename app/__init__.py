"""MCP Server – Modular Command Provider.

Exposes arbitrary terminal commands as reusable tools for a language model.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mcp-server")
except PackageNotFoundError:  # Not installed (e.g. running from source)
    __version__ = "0.0.0-dev"
