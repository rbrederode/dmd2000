#!/usr/bin/env bash
set -euo pipefail

echo "===================================="
echo " PRESTO Installer for Raspberry Pi "
echo "===================================="

PRESTO_REPO="${PRESTO_REPO:-https://github.com/scottransom/presto.git}"
PRESTO_DIR="${PRESTO_DIR:-$HOME/presto5}"
PRESTO_PREFIX="${PRESTO_PREFIX:-$PRESTO_DIR/installation}"
PRESTO_VENV="${PRESTO_VENV:-$HOME/.venvs/presto5}"

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

find_existing_dir() {
    for candidate in "$@"; do
        if [ -d "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

install_raspberry_pi_dependencies() {
    require_command apt-get "This installer expects Raspberry Pi OS or another apt-based Pi Linux."
    require_command sudo "sudo is required to install system packages."

    if [ "${SKIP_APT_UPDATE:-0}" != "1" ]; then
        echo "Updating apt package index..."
        sudo apt-get update
    else
        echo "Skipping apt package index refresh (SKIP_APT_UPDATE=1)."
    fi

    echo "Installing PRESTO build dependencies..."
    sudo apt-get install -y \
        git \
        build-essential \
        libfftw3-bin \
        libfftw3-dev \
        libgsl-dev \
        pgplot5 \
        libglib2.0-dev \
        libcfitsio-bin \
        libcfitsio-dev \
        libpng-dev \
        gfortran \
        tcsh \
        autoconf \
        libx11-dev \
        python3-dev \
        python3-numpy \
        python3-pip \
        python3-venv \
        pkg-config
}

prepare_python_environment() {
    require_command python3 "Install python3 and python3-venv, then rerun this script."

    if [ ! -d "$PRESTO_VENV" ]; then
        echo "Creating Python virtual environment at $PRESTO_VENV..."
        python3 -m venv "$PRESTO_VENV"
    fi

    # shellcheck disable=SC1090
    source "$PRESTO_VENV/bin/activate"

    python -m pip install --upgrade pip
    python -m pip install meson meson-python ninja numpy
}

configure_environment() {
    local tempo_dir
    local pgplot_dir

    if [ -n "${TEMPO:-}" ] && [ -d "$TEMPO" ]; then
        tempo_dir="$TEMPO"
    elif tempo_dir="$(find_existing_dir "$HOME/tempo" /opt/tempo /usr/local/tempo 2>/dev/null)"; then
        :
    else
        echo "ERROR: TEMPO is required to build PRESTO, but no TEMPO directory was found."
        echo "Set TEMPO to your TEMPO checkout or install TEMPO first, then rerun this script."
        exit 1
    fi

    if [ -n "${PGPLOT_DIR:-}" ] && [ -d "$PGPLOT_DIR" ]; then
        pgplot_dir="$PGPLOT_DIR"
    elif pgplot_dir="$(find_existing_dir /usr/lib/pgplot5 /usr/lib/*/pgplot5 /usr/local/lib/pgplot5 2>/dev/null)"; then
        :
    else
        echo "ERROR: PGPLOT_DIR could not be found. Install pgplot5 or set PGPLOT_DIR manually."
        exit 1
    fi

    export PRESTO="$PRESTO_DIR"
    export TEMPO="$tempo_dir"
    export PGPLOT_DIR="$pgplot_dir"
    export PATH="$PRESTO_PREFIX/bin:$PATH"

    # Keep the build-time library search path pointed at the install prefix.
    export LIBRARY_PATH="$PRESTO_PREFIX/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
    export LD_LIBRARY_PATH="$PRESTO_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

    echo "Using PRESTO=$PRESTO"
    echo "Using TEMPO=$TEMPO"
    echo "Using PGPLOT_DIR=$PGPLOT_DIR"
}

clone_or_update_presto() {
    require_command git "Install git and rerun this script."

    if [ -d "$PRESTO_DIR/.git" ]; then
        echo "Updating existing PRESTO checkout in $PRESTO_DIR..."
        git -C "$PRESTO_DIR" pull --ff-only
    else
        echo "Cloning PRESTO into $PRESTO_DIR..."
        rm -rf "$PRESTO_DIR"
        git clone "$PRESTO_REPO" "$PRESTO_DIR"
    fi
}

build_and_install_presto() {
    cd "$PRESTO_DIR"

    if [ -d build ]; then
        meson setup build --reconfigure --prefix="$PRESTO_PREFIX"
    else
        meson setup build --prefix="$PRESTO_PREFIX"
    fi

    python check_meson_build.py
    meson compile -C build
    meson install -C build

    echo ""
    echo "Installing PRESTO Python modules..."
    cd "$PRESTO_DIR/python"
    pip install --config-settings=builddir=build .
}

post_install_summary() {
    echo ""
    echo "PRESTO installation complete."
    echo ""
    echo "To use this install in a new shell, set:"
    echo "  export PRESTO=\"$PRESTO_DIR\""
    echo "  export TEMPO=\"$TEMPO\""
    echo "  export PGPLOT_DIR=\"$PGPLOT_DIR\""
    echo "  export PATH=\"$PRESTO_PREFIX/bin:\$PATH\""
    echo "  export LD_LIBRARY_PATH=\"$PRESTO_PREFIX/lib:\$LD_LIBRARY_PATH\""
    echo ""
    echo "Useful next checks: prepfold, python tests/test_presto_python.py, and $PRESTO_DIR/build/src/makewisdom"
}

case "$(uname -s)" in
    Linux)
        if is_raspberry_pi; then
            echo "Detected Raspberry Pi Linux."
        else
            echo "ERROR: Linux detected, but this does not appear to be a Raspberry Pi."
            echo "This script only runs PRESTO install commands on Raspberry Pi hardware."
            exit 1
        fi
        ;;
    *)
        echo "ERROR: This installer is intended for Raspberry Pi Linux only."
        exit 1
        ;;
esac

install_raspberry_pi_dependencies
prepare_python_environment
configure_environment
clone_or_update_presto
build_and_install_presto
post_install_summary