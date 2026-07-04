#!/usr/bin/env bash
set -euo pipefail

echo "==================================="
echo " SoapySDR Library and Tools Installer "
echo "==================================="

SOAPY_AIRSPY_REPO="https://github.com/pothosware/SoapyAirspy.git"
SOAPY_AIRSPY_DIR="${SOAPY_AIRSPY_DIR:-/private/tmp/SoapyAirspy}"
HOMEBREW_PREFIX="${HOMEBREW_PREFIX:-/opt/homebrew}"

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

install_homebrew_formula() {
    local formula="$1"

    if brew list "$formula" >/dev/null 2>&1; then
        echo "$formula is already installed."
    else
        echo "Installing $formula with Homebrew..."
        brew install "$formula"
    fi
}

python_imports_soapy() {
    python - <<'PY'
import importlib.util
raise SystemExit(0 if importlib.util.find_spec("SoapySDR") else 1)
PY
}

add_homebrew_python_binding_to_venv() {
    local binding_dir="$HOMEBREW_PREFIX/lib/python3.11/site-packages"

    if [ ! -d "$binding_dir" ]; then
        echo "WARNING: Homebrew SoapySDR Python binding directory not found: $binding_dir"
        return
    fi

    if python_imports_soapy; then
        echo "SoapySDR Python bindings already available to current Python."
        return
    fi

    local site_packages
    site_packages="$(python - <<'PY'
import site
paths = site.getsitepackages()
print(paths[0] if paths else "")
PY
)"

    if [ -z "$site_packages" ] || [ ! -d "$site_packages" ]; then
        echo "WARNING: Could not determine current Python site-packages directory."
        echo "Add this path to PYTHONPATH when running the digitiser: $binding_dir"
        return
    fi

    echo "Adding Homebrew SoapySDR Python binding path to current Python environment..."
    printf '%s\n' "$binding_dir" > "$site_packages/homebrew-soapysdr.pth"
}

install_soapy_airspy_from_source_macos() {
    require_command git "Install git and rerun this script."
    require_command cmake "Install cmake and rerun this script."

    if [ -f "$HOMEBREW_PREFIX/lib/SoapySDR/modules0.8/libairspySupport.so" ]; then
        echo "Soapy Airspy module is already installed."
        return
    fi

    if SoapySDRUtil --info 2>/dev/null | grep -qi "airspy"; then
        echo "Soapy Airspy module is already installed."
        return
    fi

    if [ -d "$SOAPY_AIRSPY_DIR/.git" ]; then
        echo "Updating existing SoapyAirspy source tree..."
        git -C "$SOAPY_AIRSPY_DIR" pull --ff-only
    else
        echo "Cloning SoapyAirspy source..."
        rm -rf "$SOAPY_AIRSPY_DIR"
        git clone "$SOAPY_AIRSPY_REPO" "$SOAPY_AIRSPY_DIR"
    fi

    mkdir -p "$SOAPY_AIRSPY_DIR/compat"
    touch "$SOAPY_AIRSPY_DIR/compat/ciso646"

    local cxx_header_dir="/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1"
    local cxx_flags="-I$SOAPY_AIRSPY_DIR/compat"
    if [ -d "$cxx_header_dir" ]; then
        cxx_flags="$cxx_flags -I$cxx_header_dir"
    fi

    echo "Building SoapyAirspy module..."
    cmake -S "$SOAPY_AIRSPY_DIR" -B "$SOAPY_AIRSPY_DIR/build" \
        -DCMAKE_INSTALL_PREFIX="$HOMEBREW_PREFIX" \
        -DCMAKE_CXX_STANDARD=17 \
        -DCMAKE_CXX_STANDARD_REQUIRED=ON \
        -DCMAKE_CXX_FLAGS="$cxx_flags"
    cmake --build "$SOAPY_AIRSPY_DIR/build"
    cmake --install "$SOAPY_AIRSPY_DIR/build"
}

install_macos() {
    echo "Detected macOS."
    require_command brew "Install Homebrew from https://brew.sh/ and rerun this script."
    require_command python "Activate the Python environment used by dmd2000 and rerun this script."

    install_homebrew_formula soapysdr
    install_homebrew_formula soapyrtlsdr
    install_homebrew_formula airspy
    install_homebrew_formula cmake

    install_soapy_airspy_from_source_macos
    add_homebrew_python_binding_to_venv
}

install_raspberry_pi() {
    echo "Detected Raspberry Pi Linux."
    require_command apt-get "This installer expects Raspberry Pi OS or another apt-based Pi Linux."
    require_command sudo "sudo is required to install system packages."

    echo "Updating apt package index..."
    sudo apt-get update

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
echo "Verifying SoapySDR..."
if command -v SoapySDRUtil >/dev/null 2>&1; then
    SoapySDRUtil --info
    SoapySDRUtil --find || true
else
    echo "ERROR: SoapySDRUtil was not found after installation."
    exit 1
fi

if python_imports_soapy; then
    python - <<'PY'
import SoapySDR
print(f"SoapySDR Python bindings available: {SoapySDR.SoapySDR_getLibVersion()}")
PY
else
    echo "WARNING: SoapySDR Python bindings are still not importable by the current Python."
fi

echo ""
echo "SoapySDR installation complete."
