#!/usr/bin/env bash
# penny_transactions.sh – query penny-track receipts.
#
# Used by registry/penny_transactions.yaml.  Calls:
#   GET {PENNY_TRACK_URL}/api/receipts?from=...&to=...&limit=100
#
# Required environment:
#   PENNY_TRACK_URL     – base URL of the penny-track instance (no trailing slash)
#   PENNY_TRACK_API_KEY – API key (use a read-only API key if one exists)
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

if [[ -z "${PENNY_TRACK_URL:-}" ]]; then
    echo "ERROR: PENNY_TRACK_URL is not set." >&2
    exit 2
fi

if [[ -z "${PENNY_TRACK_API_KEY:-}" ]]; then
    echo "ERROR: PENNY_TRACK_API_KEY is not set." >&2
    exit 2
fi

# Strip trailing slash so the URL is well-formed regardless of env value.
base="${PENNY_TRACK_URL%/}"

url="${base}/api/receipts?from=${from}&to=${to}&limit=100"

# -fsS: fail on HTTP errors, but still show the error body on stderr.
resp="$(curl -fsS --max-time 20 \
    -H "X-API-Key: ${PENNY_TRACK_API_KEY}" \
    "$url")" || {
    echo "ERROR: request to penny-track failed (check URL/API key/connectivity)." >&2
    exit 1
}

# Pretty-print; if the response isn't valid JSON, fall back to raw text.
if echo "$resp" | jq -e . >/dev/null 2>&1; then
    echo "$resp" | jq .
else
    echo "$resp"
fi
