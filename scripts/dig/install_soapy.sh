#!/usr/bin/env bash
set -euo pipefail

echo "==================================="
echo " SoapySDR Library and Tools Installer "
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

    if brew list soapysdr >/dev/null 2>&1; then
        echo "soapysdr is already installed."
    else
        echo "Installing SoapySDR with Homebrew..."
        brew install soapysdr
    fi

    if brew list soapysdr-module-airspy >/dev/null 2>&1; then
        echo "soapysdr-module-airspy is already installed."
    else
        echo "Installing Soapy Airspy module..."
        brew install soapysdr-module-airspy
    fi

    if brew list soapysdr-module-rtlsdr >/dev/null 2>&1; then
        echo "soapysdr-module-rtlsdr is already installed."
    else
        echo "Installing Soapy RTL-SDR module..."
        brew install soapysdr-module-rtlsdr
    fi

    if python3 - <<'PY'
import importlib.util
raise SystemExit(0 if importlib.util.find_spec('SoapySDR') else 1)
PY
    then
        echo "SoapySDR Python bindings already available."
    else
        echo "Installing SoapySDR Python bindings with pip..."
        python3 -m pip install --user SoapySDR
    fi
}

install_raspberry_pi() {
    echo "Detected Raspberry Pi Linux."
    require_command apt-get "This installer expects Raspberry Pi OS or another apt-based Pi Linux."
    require_command sudo "sudo is required to install system packages."

    echo "Installing SoapySDR tools, modules, and Python bindings..."
    sudo apt-get install -y \
        soapysdr-tools \
        libsoapysdr-dev \
        soapysdr-module-airspy \
        soapysdr-module-rtlsdr \
        python3-soapysdr
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
echo "Verifying SoapySDRUtil..."
if command -v SoapySDRUtil >/dev/null 2>&1; then
    SoapySDRUtil --info || true
    SoapySDRUtil --find || true
else
    echo "ERROR: SoapySDRUtil was not found after installation."
    exit 1
fi

echo ""
echo "SoapySDR installation complete."
