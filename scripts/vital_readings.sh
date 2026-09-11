#!/usr/bin/env bash
# vital_readings.sh – query vital-pulse health log readings.
#
# Used by registry/vital_readings.yaml.  Calls:
#   GET {VITAL_PULSE_URL}/api/v1/logs?from=...&to=...
#
# Required environment:
#   VITAL_PULSE_URL     – base URL of the vital-pulse instance (no trailing slash)
#   VITAL_PULSE_API_KEY – API key (use the READ_ONLY_API_KEY if configured)
#
# Args (both required):
#   $1 = from (ISO 8601, inclusive)
#   $2 = to   (ISO 8601, inclusive)
#
# The API returns {data: [...], meta: {...}}.  We pass the JSON through
# as-is (with a small header) so the model sees the structured records;
# jq is only used to pretty-print and to surface API errors clearly.

set -euo pipefail

from="${1:-}"
to="${2:-}"

if [[ -z "$from" || -z "$to" ]]; then
    echo "ERROR: both 'from' and 'to' arguments are required." >&2
    exit 2
fi

if [[ -z "${VITAL_PULSE_URL:-}" ]]; then
    echo "ERROR: VITAL_PULSE_URL is not set." >&2
    exit 2
fi

if [[ -z "${VITAL_PULSE_API_KEY:-}" ]]; then
    echo "ERROR: VITAL_PULSE_API_KEY is not set." >&2
    exit 2
fi

# Strip trailing slash so the URL is well-formed regardless of env value.
base="${VITAL_PULSE_URL%/}"

url="${base}/api/v1/logs?from=${from}&to=${to}"

# -fsS: fail on HTTP errors, but still show the error body on stderr.
resp="$(curl -fsS --max-time 20 \
    -H "X-API-Key: ${VITAL_PULSE_API_KEY}" \
    "$url")" || {
    echo "ERROR: request to vital-pulse failed (check URL/API key/connectivity)." >&2
    exit 1
}

# Pretty-print; if the response isn't valid JSON, fall back to raw text.
if echo "$resp" | jq -e . >/dev/null 2>&1; then
    echo "$resp" | jq .
else
    echo "$resp"
fi
