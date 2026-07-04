#!/usr/bin/env bash
set -euo pipefail

echo "==================================="
echo " Gqrx Installer for Raspberry Pi "
echo "==================================="

GQRX_REPO="${GQRX_REPO:-https://github.com/gqrx-sdr/gqrx.git}"
GQRX_VERSION="${GQRX_VERSION:-v2.17.7}"
GQRX_SOURCE_DIR="${GQRX_SOURCE_DIR:-$HOME/src/gqrx}"
GQRX_INSTALL_PREFIX="${GQRX_INSTALL_PREFIX:-/usr/local}"
GQRX_BUILD_JOBS="${GQRX_BUILD_JOBS:-2}"

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

    echo "Installing Gqrx build dependencies..."
    sudo apt-get install -y \
        git \
        cmake \
        build-essential \
        pkg-config \
        gnuradio-dev \
        gr-osmosdr \
        libvolk2-dev \
        libboost-all-dev \
        liblog4cpp5-dev \
        libspdlog-dev \
        qtbase5-dev \
        qttools5-dev \
        qttools5-dev-tools \
        libqt5svg5-dev \
        libpulse-dev \
        libasound2-dev

    require_command git "Install git and rerun this script."
    require_command cmake "Install cmake and rerun this script."

    mkdir -p "$(dirname "$GQRX_SOURCE_DIR")"

    if [ -d "$GQRX_SOURCE_DIR/.git" ]; then
        echo "Updating existing Gqrx source tree at $GQRX_SOURCE_DIR..."
        git -C "$GQRX_SOURCE_DIR" fetch --tags --prune
    else
        echo "Cloning Gqrx source into $GQRX_SOURCE_DIR..."
        rm -rf "$GQRX_SOURCE_DIR"
        git clone "$GQRX_REPO" "$GQRX_SOURCE_DIR"
    fi

    echo "Checking out Gqrx $GQRX_VERSION..."
    git -C "$GQRX_SOURCE_DIR" checkout "$GQRX_VERSION"

    echo "Configuring Gqrx build..."
    cmake -S "$GQRX_SOURCE_DIR" -B "$GQRX_SOURCE_DIR/build" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX="$GQRX_INSTALL_PREFIX" \
        -DFORCE_QT5=ON

    echo "Building Gqrx with $GQRX_BUILD_JOBS job(s)..."
    cmake --build "$GQRX_SOURCE_DIR/build" -j "$GQRX_BUILD_JOBS"

    echo "Installing Gqrx to $GQRX_INSTALL_PREFIX..."
    sudo cmake --install "$GQRX_SOURCE_DIR/build"
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
    gqrx --version || true
else
    echo "WARNING: gqrx was not found on PATH after installation."
    echo "The source install normally places it at: $GQRX_INSTALL_PREFIX/bin/gqrx"
fi

echo ""
echo "Gqrx installation complete."
