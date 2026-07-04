#!/usr/bin/env bash
set -euo pipefail

UPDATE_ONLY=false

if [ "${1:-}" = "--update-only" ]; then
	UPDATE_ONLY=true
fi

echo "Updating apt package index..."

if command -v timedatectl >/dev/null 2>&1; then
	sudo timedatectl set-ntp true || true
fi

sudo apt-get update || true
sudo apt-get update --allow-releaseinfo-change || true
sudo apt-get update --allow-releaseinfo-change-suite || true

if ! sudo apt-get update -o Acquire::Check-Valid-Until=false; then
	echo "ERROR: apt-get update failed. Check system time and network connectivity."
	exit 1
fi

if [ "$UPDATE_ONLY" = false ]; then
	echo "Upgrading system..."
	sudo apt upgrade -y
fi