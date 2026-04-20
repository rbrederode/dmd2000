#!/bin/bash

set -euo pipefail

WINDOW_TITLE=""
SESSION_DIR=""
REPO_ROOT=""
KEEP_OPEN=true
PRINT_COMMAND=false
EXPORT_ENVS=()
WINDOW_COLUMNS=160
WINDOW_ROWS=45

usage() {
    cat <<'EOF'
Usage:
  launch_app.sh --title <title> --session-dir <dir> [--repo-root <dir>] [--env KEY=VALUE ...] [--columns <n>] [--rows <n>] [--no-keep-open] [--print-command] -- <command> [args...]

Examples:
  launch_app.sh --title dig001 --session-dir src --env GPIOZERO_PIN_FACTORY=mock -- \
    python dig/dig.py --profile jodrell --entity_id dig001

  launch_app.sh --title tm001 --session-dir src -- \
    python tm/tm.py --profile jodrell --entity_id tm001
EOF
}

escape_for_applescript() {
    local value="$1"
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    printf '%s' "$value"
}

quote_shell_words() {
    local quoted=()
    local word

    for word in "$@"; do
        quoted+=("$(printf '%q' "$word")")
    done

    printf '%s' "${quoted[*]}"
}

resolve_repo_root() {
    local launcher_dir

    if [ -n "$REPO_ROOT" ]; then
        printf '%s\n' "$REPO_ROOT"
        return 0
    fi

    if [ -d "$PWD/src" ] && [ -d "$PWD/scripts" ]; then
        printf '%s\n' "$PWD"
        return 0
    fi

    launcher_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    printf '%s\n' "$(cd "$launcher_dir/../.." && pwd)"
}

resolve_session_dir() {
    local repo_root="$1"

    case "$SESSION_DIR" in
        /*)
            printf '%s\n' "$SESSION_DIR"
            ;;
        *)
            printf '%s\n' "$repo_root/$SESSION_DIR"
            ;;
    esac
}

build_run_cmd() {
    local cmd
    local env_var
    local title_quoted
    local session_dir_quoted

    cmd="$(quote_shell_words "$@")"
    title_quoted="$(printf '%q' "$WINDOW_TITLE")"
    session_dir_quoted="$(printf '%q' "$SESSION_DIR")"

    printf "%s" "printf '\\033]0;%s\\007' $title_quoted; cd $session_dir_quoted"

    if [ "${#EXPORT_ENVS[@]}" -gt 0 ]; then
        printf ' && export'
        for env_var in "${EXPORT_ENVS[@]}"; do
            printf ' %q' "$env_var"
        done
    fi

    printf ' && %s' "$cmd"

    if [ "$KEEP_OPEN" = true ]; then
        printf '; exec "${SHELL:-/bin/bash}" -l'
    fi
}

launch_linux_terminal() {
    local cmd="$1"
    local geometry="${WINDOW_COLUMNS}x${WINDOW_ROWS}"

    if command -v x-terminal-emulator >/dev/null 2>&1; then
        x-terminal-emulator -geometry "$geometry" -e bash -ic "$cmd" &
    elif command -v lxterminal >/dev/null 2>&1; then
        lxterminal --geometry="$geometry" -e "bash -ic '$cmd'" &
    elif command -v xfce4-terminal >/dev/null 2>&1; then
        xfce4-terminal --geometry="$geometry" --command="bash -ic '$cmd'" &
    elif command -v gnome-terminal >/dev/null 2>&1; then
        gnome-terminal --geometry="$geometry" -- bash -ic "$cmd" &
    elif command -v konsole >/dev/null 2>&1; then
        konsole --geometry "$geometry" -e bash -ic "$cmd" &
    else
        echo "No supported terminal launcher found."
        echo "Tried: x-terminal-emulator, lxterminal, xfce4-terminal, gnome-terminal, konsole"
        exit 1
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --title)
            WINDOW_TITLE="$2"
            shift 2
            ;;
        --session-dir)
            SESSION_DIR="$2"
            shift 2
            ;;
        --repo-root)
            REPO_ROOT="$2"
            shift 2
            ;;
        --env)
            EXPORT_ENVS+=("$2")
            shift 2
            ;;
        --columns)
            WINDOW_COLUMNS="$2"
            shift 2
            ;;
        --rows)
            WINDOW_ROWS="$2"
            shift 2
            ;;
        --keep-open)
            KEEP_OPEN=true
            shift
            ;;
        --no-keep-open)
            KEEP_OPEN=false
            shift
            ;;
        --print-command)
            PRINT_COMMAND=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --)
            shift
            break
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [ -z "$WINDOW_TITLE" ] || [ -z "$SESSION_DIR" ] || [ "$#" -eq 0 ]; then
    usage >&2
    exit 1
fi

REPO_ROOT="$(resolve_repo_root)"
SESSION_DIR="$(resolve_session_dir "$REPO_ROOT")"

RUN_CMD="$(build_run_cmd "$@")"

if [ "$PRINT_COMMAND" = true ]; then
    printf '%s\n' "$RUN_CMD"
    exit 0
fi

if command -v osascript >/dev/null 2>&1; then
    APPLE_CMD="$(escape_for_applescript "$RUN_CMD")"
    osascript <<EOF
tell application "Terminal"
    activate
    do script "$APPLE_CMD"
    try
        set number of columns of front window to $WINDOW_COLUMNS
        set number of rows of front window to $WINDOW_ROWS
    end try
end tell
EOF
else
    launch_linux_terminal "$RUN_CMD"
fi
