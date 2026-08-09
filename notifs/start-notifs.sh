#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp/kittyproto-$UID}/kittyproto-notifs"
PIDFILE="$RUNTIME_BASE/daemon.pid"
LOGDIR="${XDG_STATE_HOME:-$HOME/.local/state}/kittyproto"
LOGFILE="$LOGDIR/notifs.log"

if ! python -c 'import dbus_next' >/dev/null 2>&1; then
    echo "Missing dependency: python-dbus-next"
    echo "Install from the official Arch repo: sudo pacman -S python-dbus-next"
    exit 1
fi

mkdir -p "$RUNTIME_BASE" "$LOGDIR"
if [[ -f "$PIDFILE" ]]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        echo "kittyproto notifs already running (PID $pid)"
        exit 0
    fi
    rm -f "$PIDFILE"
fi

# Do not silently queue behind another notification server. The daemon itself
# will print a clear error if Dunst (or anything else) still owns the bus name.
nohup python "$ROOT/daemon.py" >>"$LOGFILE" 2>&1 &
sleep 0.2

if [[ -f "$PIDFILE" ]]; then
    echo "kittyproto notifs started (PID $(cat "$PIDFILE"))"
    echo "log: $LOGFILE"
else
    echo "kittyproto notifs did not stay up. Check: $LOGFILE"
    exit 1
fi
