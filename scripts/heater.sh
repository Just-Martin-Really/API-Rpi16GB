#!/usr/bin/env bash
# Usage: heater.sh on|off
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_relay-lib.sh"

case "${1:-}" in
    on)  relay_send HEAT_ON ;;
    off) relay_send HEAT_OFF ;;
    *)
        echo "usage: $(basename "$0") on|off" >&2
        exit 2
        ;;
esac
