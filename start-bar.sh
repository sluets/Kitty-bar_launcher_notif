#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT="${1:-}"

if [[ -z "$OUTPUT" ]]; then
    echo "usage: $0 OUTPUT"
    echo "example: $0 DP-2"
    exit 2
fi

RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp/kittyproto-$UID}/kittyproto-bar"
SAFE_OUTPUT="${OUTPUT//[^A-Za-z0-9_.-]/_}"
PIDFILE="$RUNTIME_BASE/bar-${SAFE_OUTPUT}.supervisor.pid"
STOPFILE="$RUNTIME_BASE/bar-${SAFE_OUTPUT}.stop"
LOGDIR="${XDG_STATE_HOME:-$HOME/.local/state}/kittyproto"
LOGFILE="$LOGDIR/bar-${SAFE_OUTPUT}.log"

mkdir -p "$RUNTIME_BASE" "$LOGDIR"
rm -f "$STOPFILE"

if [[ -f "$PIDFILE" ]]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        echo "kittyproto bar supervisor already running on $OUTPUT (PID $pid)"
        exit 0
    fi
    rm -f "$PIDFILE"
fi

nohup "$ROOT/bar-supervisor.sh" "$OUTPUT" >/dev/null 2>&1 &
pid=$!
echo "$pid" >"$PIDFILE"

sleep 0.1
if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "kittyproto bar supervisor failed on $OUTPUT"
    echo "log: $LOGFILE"
    exit 1
fi

echo "kittyproto bar supervisor started on $OUTPUT (PID $pid)"
