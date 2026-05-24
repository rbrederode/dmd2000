#!/usr/bin/env bash
set -euo pipefail

echo "==================================="
echo " Gqrx Installer for Raspberry Pi "
echo "==================================="

require_command() {
    local cmd="$1"
    local hint="$2"

    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ERROR: Required command '$cmd' was not found."
        echo "$hint"
        exit 1
    fi
}

is_raspberry_pi() {
    if [ -r /proc/device-tree/model ] && grep -qi "raspberry pi" /proc/device-tree/model; then
        return 0
    fi

    if [ -r /sys/firmware/devicetree/base/model ] && grep -qi "raspberry pi" /sys/firmware/devicetree/base/model; then
        return 0
    fi

    return 1
}

install_raspberry_pi() {
    echo "Detected Raspberry Pi Linux."
    require_command apt-get "This installer expects Raspberry Pi OS or another apt-based Pi Linux."
    require_command sudo "sudo is required to install system packages."

    if [ "${SKIP_APT_UPDATE:-0}" != "1" ]; then
        echo "Updating apt package index..."
        sudo apt-get update
    else
        echo "Skipping apt package index refresh (SKIP_APT_UPDATE=1)."
    fi

    echo "Installing gqrx without recommended packages..."
    echo "This avoids optional SDR extras such as xtrx-dkms unless you install them separately."
    sudo apt-get install -y --no-install-recommends gqrx-sdr
}

case "$(uname -s)" in
    Linux)
        if is_raspberry_pi; then
            install_raspberry_pi
        else
            echo "ERROR: Linux detected, but this does not appear to be a Raspberry Pi."
            echo "This script only runs Raspberry Pi install commands on Raspberry Pi hardware."
            exit 1
        fi
        ;;
    *)
        echo "ERROR: This installer is intended for Raspberry Pi Linux only."
        exit 1
        ;;
esac

echo ""
echo "Verifying gqrx..."
if command -v gqrx >/dev/null 2>&1; then
    echo "gqrx was found at: $(command -v gqrx)"
else
    echo "WARNING: gqrx was not found on PATH after installation."
    echo "If the package installed successfully, log out and back in or check whether the desktop session exposes gqrx on PATH."
fi

echo ""
echo "Gqrx installation complete."