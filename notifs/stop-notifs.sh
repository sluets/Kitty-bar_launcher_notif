#!/usr/bin/env bash
set -euo pipefail
RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp/kittyproto-$UID}/kittyproto-notifs"
PIDFILE="$RUNTIME_BASE/daemon.pid"

if [[ ! -f "$PIDFILE" ]]; then
    echo "kittyproto notifs is not running"
    exit 0
fi
pid="$(cat "$PIDFILE" 2>/dev/null || true)"
if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid"
    for _ in {1..20}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.05
    done
fi
rm -f "$PIDFILE"
echo "kittyproto notifs stopped"
