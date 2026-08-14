#!/usr/bin/env bash
# log_read.sh – read the tail of the MCP log file.
#
# Used by registry/log_read.yaml.  Prints the last N lines of the log
# file (default 50).  The log file path is determined the same way as
# log.sh:
#   1. MCP_LOG_FILE environment variable
#   2. MCP_LOG_DIR environment variable + "/mcp.log"
#   3. Default: /tmp/mcp/mcp.log
#
# When MCP_LOG_ENABLED=false, prints nothing and exits 0 (the log is
# effectively empty).  This keeps the tool functional without errors
# even when logging is disabled.

# Check if logging is disabled
if [[ "${MCP_LOG_ENABLED,,}" == "false" ]]; then
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

# Default line count
lines="${1:-50}"

# Use -e (exists) instead of -f (regular file) so that special files
# like /dev/null don't cause a false "does not exist" error.
if [[ ! -e "$logFile" ]]; then
    echo "Log file does not exist yet: $logFile" >&2
    exit 1
fi

tail -n "$lines" "$logFile"
