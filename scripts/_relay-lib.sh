#!/usr/bin/env bash
# Shared helper for actuator scripts. Source this, then call: relay_send FAN_ON
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
    echo "error: $SCRIPT_DIR/.env not found. Copy .env.example to .env and fill in credentials." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1091
source "$SCRIPT_DIR/.env"
set +a

: "${BACKEND_URL:?BACKEND_URL not set in .env}"
: "${DASHBOARD_USER:?DASHBOARD_USER not set in .env}"
: "${DASHBOARD_PW:?DASHBOARD_PW not set in .env}"
: "${ACTUATOR_ID:?ACTUATOR_ID not set in .env}"

if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is required (brew install jq, or apt install jq on the Pi)" >&2
    exit 1
fi

relay_send() {
    local command="$1"

    local login_response
    login_response=$(curl -sk -w '\n%{http_code}' "$BACKEND_URL/auth/login" \
        -H 'content-type: application/json' \
        -d "$(jq -nc --arg u "$DASHBOARD_USER" --arg p "$DASHBOARD_PW" '{username:$u, password:$p}')")

    local login_body login_status
    login_body=$(printf '%s' "$login_response" | sed '$d')
    login_status=$(printf '%s' "$login_response" | tail -n1)

    if [[ "$login_status" != "200" ]]; then
        echo "error: login failed (HTTP $login_status): $login_body" >&2
        exit 1
    fi

    local token
    token=$(printf '%s' "$login_body" | jq -r '.token // empty')
    if [[ -z "$token" ]]; then
        echo "error: no token in login response: $login_body" >&2
        exit 1
    fi

    local cmd_response cmd_body cmd_status
    cmd_response=$(curl -sk -w '\n%{http_code}' -X POST "$BACKEND_URL/api/v1/actuator-command" \
        -H "authorization: Bearer $token" \
        -H 'content-type: application/json' \
        -d "$(jq -nc --arg a "$ACTUATOR_ID" --arg c "$command" '{actuator_id:$a, command:$c}')")

    cmd_body=$(printf '%s' "$cmd_response" | sed '$d')
    cmd_status=$(printf '%s' "$cmd_response" | tail -n1)

    if [[ "$cmd_status" != "201" ]]; then
        echo "error: command failed (HTTP $cmd_status): $cmd_body" >&2
        exit 1
    fi

    echo "queued: $command -> $ACTUATOR_ID"
}
