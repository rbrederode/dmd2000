#!/bin/bash

set -euo pipefail

SESSION_DIR="$HOME/Documents/alston-rt/src"
VENV_DIR="$HOME/Documents/alston-rt/venv"

build_cmd() {
    local script_name="$1"
    local setup_cmd=""

    if [ -f "$VENV_DIR/bin/activate" ]; then
        setup_cmd="source \"$VENV_DIR/bin/activate\" && "
    fi

    printf 'cd "%s" && %sexport PYTHONPATH="%s:${PYTHONPATH:-}" && ./%s; exec bash' \
        "$SESSION_DIR" "$setup_cmd" "$SESSION_DIR" "$script_name"
}

CMD1="$(build_cmd "mdsim.sh")"
CMD2="$(build_cmd "ws001.sh")"
CMD3="$(build_cmd "dig002.sh")"

launch_linux_terminal() {
    local cmd="$1"

    if command -v x-terminal-emulator >/dev/null 2>&1; then
        x-terminal-emulator -e bash -ic "$cmd" &
    elif command -v lxterminal >/dev/null 2>&1; then
        lxterminal -e "bash -ic '$cmd'" &
    elif command -v xfce4-terminal >/dev/null 2>&1; then
        xfce4-terminal --command="bash -ic '$cmd'" &
    elif command -v gnome-terminal >/dev/null 2>&1; then
        gnome-terminal -- bash -ic "$cmd" &
    elif command -v konsole >/dev/null 2>&1; then
        konsole -e bash -ic "$cmd" &
    else
        echo "No supported terminal launcher found."
        echo "Tried: x-terminal-emulator, lxterminal, xfce4-terminal, gnome-terminal, konsole"
        exit 1
    fi
}

if command -v osascript >/dev/null 2>&1; then
    osascript <<EOF
tell application "Terminal"
    activate
    do script "$CMD1"
    delay 0.2
    do script "$CMD2"
    delay 0.2
    do script "$CMD3"
end tell
EOF
else
    launch_linux_terminal "$CMD1"
    sleep 0.2
    launch_linux_terminal "$CMD2"
    sleep 0.2
    launch_linux_terminal "$CMD3"
fi
