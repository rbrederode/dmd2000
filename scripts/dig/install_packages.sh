#!/usr/bin/env bash
set -e

PACKAGES=(
    git
    python3
    python3-pip
    python3-gpiozero
    python3-lgpio
    build-essential
    swig
    liblgpio-dev
    libglib2.0-dev
    htop
    btop
    curl
    tmux
    tk-dev
    tcl-dev
    samba
    samba-common-bin
)

for pkg in "${PACKAGES[@]}"; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        echo "Installing $pkg"
        sudo apt install -y "$pkg"
    else
        echo "$pkg already installed"
    fi
done
