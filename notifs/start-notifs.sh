#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp/kittyproto-$UID}/kittyproto-notifs"
PIDFILE="$RUNTIME_BASE/daemon.pid"
LOCKFILE="$RUNTIME_BASE/start.lock"
LOGDIR="${XDG_STATE_HOME:-$HOME/.local/state}/kittyproto"
LOGFILE="$LOGDIR/notifs.log"

if ! python -c 'import dbus_next' >/dev/null 2>&1; then
    echo "Missing dependency: python-dbus-next"
    echo "Install from the official Arch repo: sudo pacman -S python-dbus-next"
    exit 1
fi

mkdir -p "$RUNTIME_BASE" "$LOGDIR"

# Serialize simultaneous autostart attempts. This makes duplicate Hyprland
# exec lines harmless instead of racing two daemons against the DBus name.
exec 9>"$LOCKFILE"
flock 9

if [[ -f "$PIDFILE" ]]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
        echo "kittyproto notifs already running (PID $pid)"
        exit 0
    fi
    rm -f "$PIDFILE"
fi

# Recover from a missing/stale pidfile if the exact daemon is nevertheless
# alive. Do not launch a second copy.
existing="$(pgrep -f -- "^python(3)? .*${ROOT//\//\\/}/daemon\\.py$" | head -n1 || true)"
if [[ -n "$existing" ]] && kill -0 "$existing" 2>/dev/null; then
    printf '%s\n' "$existing" >"$PIDFILE"
    echo "kittyproto notifs already running (PID $existing)"
    exit 0
fi

nohup python "$ROOT/daemon.py" >>"$LOGFILE" 2>&1 &
child=$!

# daemon.py writes daemon.pid only after it successfully owns
# org.freedesktop.Notifications. Wait for that handshake instead of sleeping a
# fixed 0.2 seconds and guessing.
for _ in {1..40}; do
    if [[ -f "$PIDFILE" ]]; then
        pid="$(cat "$PIDFILE" 2>/dev/null || true)"
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            echo "kittyproto notifs started (PID $pid)"
            echo "log: $LOGFILE"
            exit 0
        fi
    fi

    if ! kill -0 "$child" 2>/dev/null; then
        break
    fi
    sleep 0.05
done

rm -f "$PIDFILE"
echo "kittyproto notifs did not stay up. Check: $LOGFILE"
exit 1
