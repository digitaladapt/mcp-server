#!/usr/bin/env bash
# log_read.sh – read the tail of the MCP log file.
#
# Used by registry/log_read.yaml.  Prints the last N lines of the log
# file (default 50).  The log file path is determined the same way as
# log.sh:
#   1. MCP_LOG_FILE environment variable
#   2. MCP_LOG_DIR environment variable + "/mcp.log"
#   3. Default: /tmp/mcp/mcp.log

# Determine log file path
if [[ -n "$MCP_LOG_FILE" ]]; then
    logFile="$MCP_LOG_FILE"
elif [[ -n "$MCP_LOG_DIR" ]]; then
    logFile="$MCP_LOG_DIR/mcp.log"
else
    logFile="/tmp/mcp/mcp.log"
fi

# Default line count
lines="${1:-50}"

if [[ ! -f "$logFile" ]]; then
    echo "Log file does not exist yet: $logFile" >&2
    exit 1
fi

tail -n "$lines" "$logFile"
