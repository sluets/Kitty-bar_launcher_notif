#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp/kittyproto-$UID}/kittyproto-notifs"
PIDFILE="$RUNTIME_BASE/daemon.pid"

pid=""
if [[ -f "$PIDFILE" ]]; then
    candidate="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "$candidate" ]] && kill -0 "$candidate" 2>/dev/null; then
        pid="$candidate"
    fi
fi

# If the pidfile vanished or went stale, locate the exact daemon script.
if [[ -z "$pid" ]]; then
    pid="$(pgrep -f -- "^python(3)? .*${ROOT//\//\\/}/daemon\\.py$" | head -n1 || true)"
fi

if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PIDFILE"
    echo "kittyproto notifs is not running"
    exit 0
fi

kill -TERM "$pid"
for _ in {1..40}; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.05
done

if kill -0 "$pid" 2>/dev/null; then
    echo "kittyproto notifs did not stop cleanly (PID $pid)" >&2
    exit 1
fi

rm -f "$PIDFILE"
echo "kittyproto notifs stopped"
