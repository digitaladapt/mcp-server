# ── MCP Server – PHP Variant ───────────────────────────────────────────
# Layers PHP CLI on top of the base mcp-server image so registry
# definitions can wrap PHP scripts.
#
# Build (from repo root):
#   docker build -f variants/Dockerfile.php -t digitaladapt/mcp-server:php .
#
# Run:
#   docker run -p 8000:8000 \
#     -v ./registry:/app/registry \
#     -v ./config.sh:/app/config.sh:ro \
#     digitaladapt/mcp-server:php
# ────────────────────────────────────────────────────────────────────────

FROM digitaladapt/mcp-server:latest

# Switch to root to install system packages.
USER root

# Install PHP CLI + common extensions that scripts commonly need.
RUN apt-get update && apt-get install -y --no-install-recommends \
        php-cli \
        php-curl \
        php-mbstring \
        php-xml \
    && rm -rf /var/lib/apt/lists/* \
    && php --version

# Drop back to the non-root user.
USER mcp
