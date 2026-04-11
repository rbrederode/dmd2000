#!/bin/bash

set -euo pipefail

SESSION_DIR="$HOME/github/alston-rt/src"
RUN_CMD='cd "$HOME/github/alston-rt/src" && python -m sdp.sdp --entity_id sdp001 --tm_host 192.168.0.7 --dig_host 192.168.0.7 --scan_store_dir ~/samples; exec bash'

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
    do script "cd \"$SESSION_DIR\" && python -m sdp.sdp --entity_id sdp001 --tm_host 192.168.0.7 --dig_host 192.168.0.7 --scan_store_dir ~/samples; exec bash"
end tell
EOF
else
    launch_linux_terminal "$RUN_CMD"
fi
