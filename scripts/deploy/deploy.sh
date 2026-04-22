#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

export BUILD_NUMBER="${BUILD_NUMBER:-$(git rev-list --count HEAD)}"
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

echo "Using BUILD_NUMBER=$BUILD_NUMBER"

exec python -m util.launch "$@"
