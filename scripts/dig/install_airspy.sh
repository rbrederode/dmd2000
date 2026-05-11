#!/usr/bin/env bash
set -euo pipefail

echo "==================================="
echo " Airspy Library and Tools Installer "
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

install_macos() {
    echo "Detected macOS."
    require_command brew "Install Homebrew from https://brew.sh/ and rerun this script."

    if brew list airspy >/dev/null 2>&1; then
        echo "airspy is already installed."
    else
        echo "Installing airspy with Homebrew..."
        brew install airspy
    fi
}

install_raspberry_pi() {
    echo "Detected Raspberry Pi Linux."
    require_command apt-get "This installer expects Raspberry Pi OS or another apt-based Pi Linux."
    require_command sudo "sudo is required to install system packages."

    echo "Installing Airspy tools and development libraries..."
    sudo apt-get install -y airspy libairspy-dev
}

case "$(uname -s)" in
    Darwin)
        install_macos
        ;;
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
        echo "ERROR: Unsupported operating system: $(uname -s)"
        exit 1
        ;;
esac

echo ""
echo "Verifying airspy_info..."
if command -v airspy_info >/dev/null 2>&1; then
    if AIRSPY_INFO_OUTPUT="$(airspy_info 2>&1)"; then
        echo "$AIRSPY_INFO_OUTPUT"
    else
        AIRSPY_INFO_STATUS=$?
        echo "$AIRSPY_INFO_OUTPUT"
        echo ""
        echo "airspy_info exited with status $AIRSPY_INFO_STATUS."
    fi

    if echo "$AIRSPY_INFO_OUTPUT" | grep -Eq "AIRSPY_ERROR_NOT_FOUND|failed"; then
        echo ""
        echo "airspy_info is installed, but no Airspy device could be opened."
        echo "Connect the Airspy Mini and rerun: airspy_info"
    fi
else
    echo "ERROR: airspy_info was not found after installation."
    exit 1
fi

echo ""
echo "Airspy installation complete."
