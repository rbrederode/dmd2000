#!/bin/bash

ENV_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$ENV_SCRIPT_DIR/../.." && pwd)"

export REPO_ROOT
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export BUILD_NUMBER="${BUILD_NUMBER:-$(git -C "$REPO_ROOT" rev-list --count HEAD 2>/dev/null || echo 0)}"
