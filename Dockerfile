# ── MCP Server Dockerfile ──────────────────────────────────────────────
# Multi-arch (amd64 + arm64) image for the Modular Command Provider.
#
# Build:
#   docker build -t digitaladapt/mcp-server:latest .
#
# Multi-arch (requires buildx):
#   docker buildx build --platform linux/amd64,linux/arm64 -t digitaladapt/mcp-server:latest .
#
# Run:
#   docker run -p 8000:8000 \
#     --env-file .env \
#     -v ./registry:/app/registry \
#     digitaladapt/mcp-server:latest
#
# ── Variant strategy ──────────────────────────────────────────────────
# To create a PHP or Node variant, create a new Dockerfile that starts
# FROM digitaladapt/mcp-server:latest and installs the extra runtime, e.g.:
#
#   FROM digitaladapt/mcp-server:latest
#   RUN apt-get update && apt-get install -y --no-install-recommends \
#       php-cli && rm -rf /var/lib/apt/lists/*
#   # registry/*.yaml can now reference /usr/bin/php
# ────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

# System dependencies:
#   curl, jq  – needed by discord.sh and similar CLI tools
#   tini      – proper PID-1 signal handling for uvicorn
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        jq \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user (uid 1000) for the server to run as.
RUN groupadd --system --gid 1000 mcp \
 && useradd  --system --uid 1000 --gid mcp \
             --home-dir /app --shell /usr/sbin/nologin mcp

WORKDIR /app

# Install Python dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY app/ ./app/

# Copy the built-in example command definitions.
# Users can override these by mounting a volume at /app/registry.
COPY registry/ ./registry/

# Copy helper scripts (discord.sh, etc.).
# These are referenced by registry YAMLs via relative paths.
# config.sh is NOT copied — it's a secret; mount it or use env vars.
COPY scripts/ ./scripts/

# Install the package itself so importlib.metadata can resolve __version__.
COPY pyproject.toml .
RUN pip install --no-cache-dir --no-deps .

# Switch to the non-root user.
USER mcp

# Expose the default port; can be overridden via UVICORN_PORT env.
EXPOSE 8000

# Use tini as PID 1 for proper signal forwarding.
ENTRYPOINT ["tini", "--"]

# Default command — can be overridden for variants.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
