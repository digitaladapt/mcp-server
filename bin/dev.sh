#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# MCP Server Dev Script
#
# Manages a local uvicorn dev server for end-to-end development and testing.
# Binds to 0.0.0.0 so the app is accessible via a reverse proxy (Caddy) for
# browser-based visual verification.
#
# The dev server uses a dedicated .venv-dev virtual environment so that the
# production Docker setup is never affected.
#
# Self-bootstrapping: the `start` command checks for Python, pip, and project
# dependencies — installing them automatically if missing. This means the
# script works even after a terminal reset/reboot, embracing the self-cleaning
# container design.
#
# Usage:
#   bin/dev.sh start     Start the dev server (auto-installs deps if needed)
#   bin/dev.sh stop      Stop the dev server
#   bin/dev.sh status    Check if the dev server is running
#   bin/dev.sh restart   Stop and start the dev server
#
# Port assignment (M-C-P = 6-2-7):
#   8627 → https://${MCP_DEV_URL:-mcp.example.com}
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ───────────────────────────────────────────────────────────
PORT=8627
HOST="0.0.0.0"
# Public URL of the dev server, if behind a reverse proxy.  Override with the
# MCP_DEV_URL environment variable in your shell profile (e.g. ~/.bashrc).
DEV_URL="${MCP_DEV_URL:-mcp.example.com}"
VENV_DIR=".venv-dev"
PID_FILE="var/.dev-server.pid"
LOG_FILE="var/log/dev-server.log"

# Weather data enrichment (Open-Meteo, no API key required).
# "lat,long" in decimal degrees; must be a valid location on Earth.
# Defaults to Portland, OR (45.5,-122.6).  Override with WEATHER_LOCATION.
WEATHER_LOCATION="${WEATHER_LOCATION:-45.5,-122.6}"

# System tools needed
SYSTEM_TOOLS=(
    curl
    git
)

# Python packages needed for bootstrap (pip is bundled with Python)
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10

# Resolve project root (script lives in bin/)
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Ensure var directory structure exists
mkdir -p var/log

# ── Helpers ─────────────────────────────────────────────────────────────────

is_running() {
    if [[ ! -f "$PID_FILE" ]]; then
        return 1
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
        return 1
    fi
    return 0
}

print_status() {
    if is_running; then
        local pid
        pid="$(cat "$PID_FILE")"
        echo "✅ MCP Server dev server is RUNNING"
        echo "   PID:     $pid"
        echo "   URL:     http://localhost:${PORT}"
        echo "   Exposed: http://${HOST}:${PORT}"
        echo "   Dev URL: https://${DEV_URL}"
        echo "   Venv:    ${VENV_DIR}"
        echo "   Logs:    ${LOG_FILE}"
    else
        echo "⛔ MCP Server dev server is STOPPED"
    fi
}

# ── Bootstrap ───────────────────────────────────────────────────────────────
# Ensures Python, virtual environment, and project dependencies are present.
# Idempotent — if everything is already installed, checks are fast no-ops.
# This is what makes the script survive terminal resets/reboots.

bootstrap() {
    local needed_packages=()

    # ── Check system tools ──
    for tool in "${SYSTEM_TOOLS[@]}"; do
        if ! command -v "$tool" &>/dev/null; then
            needed_packages+=("$tool")
        fi
    done

    # ── Check Python ──
    local python_needs_install=false
    if ! command -v python3 &>/dev/null; then
        python_needs_install=true
    else
        # Verify minimum version
        local py_version
        py_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")"
        local py_major py_minor
        py_major="${py_version%%.*}"
        py_minor="${py_version#*.}"
        if [[ "$py_major" -lt "$PYTHON_MIN_MAJOR" ]] || \
           [[ "$py_major" -eq "$PYTHON_MIN_MAJOR" && "$py_minor" -lt "$PYTHON_MIN_MINOR" ]]; then
            python_needs_install=true
        fi
    fi

    if [[ "$python_needs_install" == "true" ]]; then
        needed_packages+=("python3" "python3-pip" "python3-venv")
    fi

    # ── Install missing packages ──
    if [[ ${#needed_packages[@]} -gt 0 ]]; then
        echo "→ Installing missing system packages: ${needed_packages[*]}…"
        sudo apt-get update -qq
        sudo apt-get install -y -qq "${needed_packages[@]}"
    fi

    # ── Ensure pip is available in Python ──
    if ! python3 -m pip --version &>/dev/null; then
        echo "→ Installing pip…"
        curl -sS https://bootstrap.pypa.io/get-pip.py | python3
    fi

    # ── Ensure virtual environment exists ──
    if [[ ! -d "$VENV_DIR" ]]; then
        echo "→ Creating virtual environment (${VENV_DIR})…"
        python3 -m venv "$VENV_DIR"
    fi

    # ── Ensure project dependencies are installed ──
    local venv_python="$VENV_DIR/bin/python"
    local needs_deps=false

    if ! "$venv_python" -c "import fastapi, uvicorn, pydantic, yaml, httpx" 2>/dev/null; then
        needs_deps=true
    fi

    if [[ "$needs_deps" == "true" ]]; then
        echo "→ Installing project dependencies…"
        "$venv_python" -m pip install --quiet --upgrade pip
        "$venv_python" -m pip install --quiet -e ".[dev]"
    fi
}

# ── Commands ────────────────────────────────────────────────────────────────

start() {
    if is_running; then
        echo "⚠️  Dev server is already running (PID $(cat "$PID_FILE"))"
        print_status
        exit 0
    fi

    echo "→ Starting MCP Server dev server on ${HOST}:${PORT}…"

    # Self-bootstrap: ensure all dependencies are present
    bootstrap

    local venv_python="$VENV_DIR/bin/python"
    local venv_uvicorn="$VENV_DIR/bin/uvicorn"

    echo "→ Starting uvicorn dev server…"
    MCP_LOG_FILE="${PROJECT_ROOT}/var/log/mcp.log" \
    WEATHER_LOCATION="${WEATHER_LOCATION}" \
    nohup "$venv_uvicorn" app.main:app \
        --host "$HOST" \
        --port "$PORT" \
        --reload \
        > "$LOG_FILE" 2>&1 &

    local pid=$!
    echo "$pid" > "$PID_FILE"

    # Give it a moment to boot
    sleep 3

    if is_running; then
        echo ""
        print_status
    else
        echo "❌ Failed to start dev server. Check logs:"
        echo "   ${LOG_FILE}"
        tail -20 "$LOG_FILE" 2>/dev/null || true
        rm -f "$PID_FILE"
        exit 1
    fi
}

stop() {
    if ! is_running; then
        echo "⚠️  Dev server is not running."
        rm -f "$PID_FILE"
        exit 0
    fi

    local pid
    pid="$(cat "$PID_FILE")"
    echo "→ Stopping dev server (PID ${pid})…"
    kill "$pid" 2>/dev/null || true

    # Wait for graceful shutdown
    local count=0
    while kill -0 "$pid" 2>/dev/null && [[ $count -lt 10 ]]; do
        sleep 0.5
        count=$((count + 1))
    done

    # Force kill if still alive
    if kill -0 "$pid" 2>/dev/null; then
        echo "→ Process didn't exit gracefully, sending SIGKILL…"
        kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$PID_FILE"
    echo "✅ Dev server stopped."
}

restart() {
    stop
    sleep 1
    start
}

# ── Main ────────────────────────────────────────────────────────────────────

usage() {
    echo "Usage: bin/dev.sh {start|stop|status|restart}"
    echo ""
    echo "Commands:"
    echo "  start     Start the dev server (auto-installs deps if needed)"
    echo "  stop      Stop the dev server"
    echo "  status    Check if the dev server is running"
    echo "  restart   Restart the dev server"
    exit 1
}

case "${1:-}" in
    start)   start ;;
    stop)    stop ;;
    status)  print_status ;;
    restart) restart ;;
    *)       usage ;;
esac
