#!/usr/bin/env bash
# log.sh – append a timestamped message to a log file.
#
# Used by registry/log.yaml.  Supports a positional `message` argument
# and an optional `--level` flag (info, warn, error — defaults to info).
#
# The log file path is determined by:
#   1. MCP_LOG_FILE environment variable (highest priority)
#   2. MCP_LOG_DIR environment variable + "/mcp.log"
#   3. Default: /tmp/mcp/mcp.log
#
# Set MCP_LOG_ENABLED=false to disable logging entirely.
# When disabled, the formatted line is still echoed to stdout (so the
# caller sees a success response) but nothing is written to disk.
# This is useful in environments where you don't want log files to
# accumulate (e.g. ephemeral containers) but still want the MCP tool
# to function without errors.
#
# If the parent directory doesn't exist, it is created.
# The formatted line is also echoed to stdout for the caller.

level="INFO"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --level)
            shift
            level=$(echo "$1" | tr '[:lower:]' '[:upper:]')
            shift
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

message="$1"

# Build the timestamped line
timestamp=$(date '+%Y-%m-%dT%H:%M:%S%z')
line="[$timestamp] [$level] $message"

# Check if logging is disabled
if [[ "${MCP_LOG_ENABLED,,}" == "false" ]]; then
    # Still echo to stdout so the caller sees the formatted line
    echo "$line"
    exit 0
fi

# Determine log file path
if [[ -n "$MCP_LOG_FILE" ]]; then
    logFile="$MCP_LOG_FILE"
elif [[ -n "$MCP_LOG_DIR" ]]; then
    logFile="$MCP_LOG_DIR/mcp.log"
else
    logFile="/tmp/mcp/mcp.log"
fi

# Create parent directory if it doesn't exist
logDir=$(dirname "$logFile")
if [[ ! -d "$logDir" ]]; then
    mkdir -p "$logDir" 2>/dev/null || true
fi

# Append to the log file
echo "$line" >> "$logFile"

# Echo to stdout so the MCP caller sees the formatted line
echo "$line"
