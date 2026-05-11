#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Updating package lists..."
bash "$BASE_DIR/update_apt.sh"

echo "Upgrading system..."
sudo apt upgrade -y