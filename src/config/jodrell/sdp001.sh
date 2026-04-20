#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER="$SCRIPT_DIR/../../../scripts/launch/launch_app.sh"

exec "$LAUNCHER" \
    --title "$(basename "$0" .sh)" \
    --session-dir "src" \
    -- \
    python -m sdp.sdp --profile jodrell --entity_id sdp001 --tm_host 127.0.0.1 --dig_host 127.0.0.1 --scan_store_dir ~/samples
