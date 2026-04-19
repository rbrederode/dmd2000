#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$SCRIPT_DIR/../../../scripts/launch/launch_app.sh"

exec "$LAUNCHER" \
    --title "$(basename "$0" .sh)" \
    --session-dir "src" \
    --env "GPIOZERO_PIN_FACTORY=mock" \
    -- \
    python dig/dig.py --profile local --entity_id dig003 --tm_host 127.0.0.1 --sdp_host 127.0.0.1 --local_host 127.0.0.1
