#!/usr/bin/env bash
set -euo pipefail

echo "===================================="
echo " PRESTO Installer for Raspberry Pi "
echo "===================================="

PRESTO_REPO="${PRESTO_REPO:-https://github.com/scottransom/presto.git}"
PRESTO_DIR="${PRESTO_DIR:-$HOME/presto5}"
PRESTO_PREFIX="${PRESTO_PREFIX:-$PRESTO_DIR/installation}"
PRESTO_VENV="${PRESTO_VENV:-$HOME/.venvs/presto5}"
PRESTO_ENV_FILE="${PRESTO_ENV_FILE:-$HOME/.presto_env}"
DEBIAN_PKGCONFIG_DIRS="/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/lib/pkgconfig:/usr/share/pkgconfig"

require_command() {
    local cmd="$1"
    local hint="$2"

    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "ERROR: Required command '$cmd' was not found."
        echo "$hint"
        exit 1
    fi
}

require_pkg_config_dependency() {
    local package_name="$1"
    local apt_package_hint="$2"
    local pkg_config_cmd="${PKG_CONFIG:-pkg-config}"

    if ! "$pkg_config_cmd" --exists "$package_name" >/dev/null 2>&1; then
        echo "ERROR: pkg-config dependency '$package_name' was not found."
        echo "Install the matching development package and rerun this script: $apt_package_hint"
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

configure_pkg_config() {
    local pkg_config_cmd

    if [ -x /usr/bin/pkg-config ] && /usr/bin/pkg-config --exists glib-2.0 >/dev/null 2>&1; then
        pkg_config_cmd=/usr/bin/pkg-config
    elif command -v pkg-config >/dev/null 2>&1 && pkg-config --exists glib-2.0 >/dev/null 2>&1; then
        pkg_config_cmd="$(command -v pkg-config)"
    elif [ -x /usr/bin/pkg-config ]; then
        pkg_config_cmd=/usr/bin/pkg-config
    else
        echo "ERROR: pkg-config is not available."
        echo "Install pkg-config and rerun this script."
        exit 1
    fi

    export PKG_CONFIG="$pkg_config_cmd"

    if ! "$PKG_CONFIG" --exists glib-2.0 >/dev/null 2>&1; then
        export PKG_CONFIG_PATH="$DEBIAN_PKGCONFIG_DIRS${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    fi

    if ! "$PKG_CONFIG" --exists glib-2.0 >/dev/null 2>&1; then
        echo "ERROR: pkg-config still cannot see glib-2.0."
        echo "Using PKG_CONFIG=$PKG_CONFIG"
        echo "Using PKG_CONFIG_PATH=${PKG_CONFIG_PATH:-<empty>}"
        echo "Run: $PKG_CONFIG --modversion glib-2.0"
        exit 1
    fi

    echo "Using PKG_CONFIG=$PKG_CONFIG"
    echo "Using PKG_CONFIG_PATH=${PKG_CONFIG_PATH:-<empty>}"
}

configure_environment() {
    local pgplot_dir
    local tempo_dir=""

    if [ -n "${TEMPO:-}" ] && [ -d "$TEMPO" ]; then
        tempo_dir="$TEMPO"
    else
        tempo_dir="$(find_existing_dir "$HOME/tempo" /opt/tempo /usr/local/tempo 2>/dev/null || true)"
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
    export PGPLOT_DIR="$pgplot_dir"
    export PATH="$PRESTO_PREFIX/bin:$PATH"

    # Keep the build-time library search path pointed at the install prefix.
    export LIBRARY_PATH="$PRESTO_PREFIX/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
    export LD_LIBRARY_PATH="$PRESTO_PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

    if [ -n "$tempo_dir" ]; then
        export TEMPO="$tempo_dir"
        echo "Using TEMPO=$TEMPO"
    else
        unset TEMPO || true
        echo "TEMPO not found; continuing without barycentering/polyco support."
    fi

    echo "Using PRESTO=$PRESTO"
    echo "Using PGPLOT_DIR=$PGPLOT_DIR"
}

preflight_build_dependencies() {
    require_command "$PKG_CONFIG" "Install pkg-config and rerun this script."
    require_pkg_config_dependency glib-2.0 "sudo apt-get install -y libglib2.0-dev"
    require_pkg_config_dependency fftw3f "sudo apt-get install -y libfftw3-dev"
    require_pkg_config_dependency gsl "sudo apt-get install -y libgsl-dev"
    require_pkg_config_dependency cfitsio "sudo apt-get install -y libcfitsio-dev"
    require_pkg_config_dependency x11 "sudo apt-get install -y libx11-dev"
    require_pkg_config_dependency libpng "sudo apt-get install -y libpng-dev"
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

    if [ -n "${TEMPO:-}" ]; then
        python check_meson_build.py
    else
        echo "Skipping check_meson_build.py because TEMPO is not set."
        echo "PRESTO will still build, but TEMPO-dependent features such as barycentering and polycos will be unavailable."
    fi
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
    echo "  source \"$PRESTO_ENV_FILE\""
    echo ""
    echo "Useful next checks: prepfold, python tests/test_presto_python.py, and $PRESTO_DIR/build/src/makewisdom"
    if [ -z "${TEMPO:-}" ]; then
        echo "TEMPO-specific checks will be skipped unless you install TEMPO later."
    fi
}

write_presto_env_file() {
    local bashrc_file="$HOME/.bashrc"
    local bashrc_line="[ -f \"$PRESTO_ENV_FILE\" ] && . \"$PRESTO_ENV_FILE\""

    mkdir -p "$(dirname "$PRESTO_ENV_FILE")"

    cat > "$PRESTO_ENV_FILE" <<EOF
# Generated by install_presto.sh
export PRESTO="$PRESTO_DIR"
export PGPLOT_DIR="$PGPLOT_DIR"
export PATH="$PRESTO_PREFIX/bin:\$PATH"
export LD_LIBRARY_PATH="$PRESTO_PREFIX/lib:\${LD_LIBRARY_PATH:-}"
EOF

    if [ -n "${TEMPO:-}" ]; then
        printf 'export TEMPO="%s"\n' "$TEMPO" >> "$PRESTO_ENV_FILE"
    fi

    if [ -f "$bashrc_file" ]; then
        if ! grep -Fqx "$bashrc_line" "$bashrc_file"; then
            printf '\n# PRESTO environment\n%s\n' "$bashrc_line" >> "$bashrc_file"
            echo "Added PRESTO environment source line to $bashrc_file"
        else
            echo "PRESTO environment source line already present in $bashrc_file"
        fi
    else
        printf '# PRESTO environment\n%s\n' "$bashrc_line" > "$bashrc_file"
        echo "Created $bashrc_file with PRESTO environment source line"
    fi

    # Make the current shell usable immediately if the script was sourced.
    # shellcheck disable=SC1090
    . "$PRESTO_ENV_FILE"
}

configure_dynamic_linker() {
    local ld_so_conf_file="/etc/ld.so.conf.d/presto.conf"

    if command -v sudo >/dev/null 2>&1; then
        echo "Registering $PRESTO_PREFIX/lib with the dynamic linker..."
        printf '%s\n' "$PRESTO_PREFIX/lib" | sudo tee "$ld_so_conf_file" >/dev/null
        sudo ldconfig
    else
        echo "WARNING: sudo is not available, so the dynamic linker cache was not updated."
        echo "rfifind and other PRESTO binaries will rely on source \"$PRESTO_ENV_FILE\" in new shells."
    fi
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
configure_pkg_config
preflight_build_dependencies
clone_or_update_presto
build_and_install_presto
write_presto_env_file
configure_dynamic_linker
post_install_summary