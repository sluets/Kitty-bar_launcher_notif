#!/usr/bin/env bash
set -euo pipefail

OUTPUT="${1:-}"
RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp/kittyproto-$UID}/kittyproto-bar"

stop_one() {
    local output="$1"
    local safe="${output//[^A-Za-z0-9_.-]/_}"
    local pidfile="$RUNTIME_BASE/bar-${safe}.supervisor.pid"
    local stopfile="$RUNTIME_BASE/bar-${safe}.stop"
    local childfile="$RUNTIME_BASE/bar-${safe}.child.pid"

    mkdir -p "$RUNTIME_BASE"
    touch "$stopfile"

    if [[ -f "$pidfile" ]]; then
        local pid
        pid="$(cat "$pidfile" 2>/dev/null || true)"
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            for _ in {1..40}; do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.05
            done
            kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
        fi
    fi

    if [[ -f "$childfile" ]]; then
        local child
        child="$(cat "$childfile" 2>/dev/null || true)"
        [[ -n "$child" ]] && kill "$child" 2>/dev/null || true
    fi

    rm -f "$pidfile" "$childfile" "$stopfile"
    echo "kittyproto bar stopped on $output"
}

if [[ -n "$OUTPUT" ]]; then
    stop_one "$OUTPUT"
else
    shopt -s nullglob
    files=("$RUNTIME_BASE"/bar-*.supervisor.pid)
    for pidfile in "${files[@]}"; do
        name="$(basename "$pidfile")"
        output="${name#bar-}"
        output="${output%.supervisor.pid}"
        stop_one "$output"
    done
    echo "all kittyproto bars stopped"
fi
