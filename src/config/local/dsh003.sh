#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

printf '\033]0;%s\007' "$(basename "$0" .sh)"

"$SCRIPT_DIR/ws001.sh"
sleep 0.2
"$SCRIPT_DIR/mdsim.sh"
sleep 0.2
"$SCRIPT_DIR/dig003.sh"
