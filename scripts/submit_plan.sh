#!/usr/bin/env bash
set -Eeuo pipefail
URL="${URC_API_URL:-http://127.0.0.1:8765}"
ISSUE_TYPE="${ISSUE_TYPE:-manual_plan}"
SUMMARY="${1:-${SUMMARY:-}}"
CONTEXT="${CONTEXT:-}"
PRIORITY="${PRIORITY:-medium}"
TARGET_AGENTS="${TARGET_AGENTS:-sre_diagnoser,performance_analyst,documentarian}"
REQUESTED_BY="${REQUESTED_BY:-operator}"
RUN_EXECUTOR="${RUN_EXECUTOR:-false}"
APPLY="${APPLY:-false}"

if [[ -z "$SUMMARY" ]]; then
  echo "usage: submit_plan.sh <summary>" >&2
  exit 2
fi

payload=$(cat <<JSON
{"issue_type":"$ISSUE_TYPE","summary":"$SUMMARY","context":"$CONTEXT","priority":"$PRIORITY","requested_by":"$REQUESTED_BY","target_agents":["${TARGET_AGENTS//,/","}"],"run_executor":$RUN_EXECUTOR,"apply":$APPLY}
JSON
)

curl -sS -X POST "$URL/v1/plan" -H 'Content-Type: application/json' -d "$payload"
