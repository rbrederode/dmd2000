#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../common/env.sh"
cd "$REPO_ROOT"

echo "Using BUILD_NUMBER=$BUILD_NUMBER"

exec python -m util.launch "$@"
