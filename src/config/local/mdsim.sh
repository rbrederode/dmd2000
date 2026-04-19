#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$SCRIPT_DIR/../../../scripts/launch/launch_app.sh"

exec "$LAUNCHER" \
    --title "$(basename "$0" .sh)" \
    --session-dir "src" \
    -- \
    python dsh/drivers/md01/md01_simulator.py --host 127.0.0.1
