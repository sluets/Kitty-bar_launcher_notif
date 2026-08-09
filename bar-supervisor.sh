#!/usr/bin/env bash
set -u

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT="${1:-}"

if [[ -z "$OUTPUT" ]]; then
    echo "usage: $0 OUTPUT" >&2
    exit 2
fi

RUNTIME_BASE="${XDG_RUNTIME_DIR:-/tmp/kittyproto-$UID}/kittyproto-bar"
LOGDIR="${XDG_STATE_HOME:-$HOME/.local/state}/kittyproto"
SAFE_OUTPUT="${OUTPUT//[^A-Za-z0-9_.-]/_}"
STOPFILE="$RUNTIME_BASE/bar-${SAFE_OUTPUT}.stop"
CHILDPID="$RUNTIME_BASE/bar-${SAFE_OUTPUT}.child.pid"
LOGFILE="$LOGDIR/bar-${SAFE_OUTPUT}.log"

mkdir -p "$RUNTIME_BASE" "$LOGDIR"
rm -f "$STOPFILE"

child=""

cleanup() {
    touch "$STOPFILE"
    if [[ -n "$child" ]] && kill -0 "$child" 2>/dev/null; then
        kill "$child" 2>/dev/null || true
        wait "$child" 2>/dev/null || true
    fi
    rm -f "$CHILDPID"
}
trap cleanup TERM INT EXIT

output_is_ready() {
    # Ask kitty itself which Wayland outputs are currently bindable.
    # Do not trust Hyprland's monitor list here: the OLED can exist in
    # Hyprland before kitty can safely create a layer-shell panel on it.
    kitten panel --output-name list 2>/dev/null \
        | sed -n 's/^[[:space:]]*\([^[:space:]][^[:space:]]*\)[[:space:]]*$/\1/p' \
        | grep -Fxq -- "$OUTPUT"
}

echo "[$(date '+%F %T')] supervisor started for $OUTPUT" >>"$LOGFILE"

while [[ ! -e "$STOPFILE" ]]; do
    if ! output_is_ready; then
        sleep 0.25
        continue
    fi

    echo "[$(date '+%F %T')] kitty sees $OUTPUT; launching bar" >>"$LOGFILE"

    "$ROOT/launch.py" "$OUTPUT" >>"$LOGFILE" 2>&1 &
    child=$!
    echo "$child" >"$CHILDPID"

    wait "$child"
    rc=$?
    child=""
    rm -f "$CHILDPID"

    [[ -e "$STOPFILE" ]] && break

    echo "[$(date '+%F %T')] bar on $OUTPUT exited rc=$rc; waiting for output before retry" >>"$LOGFILE"
    sleep 0.25
done

echo "[$(date '+%F %T')] supervisor stopped for $OUTPUT" >>"$LOGFILE"
