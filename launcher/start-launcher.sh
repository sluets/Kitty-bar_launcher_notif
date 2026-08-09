#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Hotkey acts as a toggle.
if pgrep -f -- "$ROOT/launcher.py" >/dev/null 2>&1; then
    pkill -TERM -f -- "$ROOT/launcher.py" || true
    exit 0
fi

kitty \
  --class kittyproto-launcher \
  --title kittyproto-launcher \
  --config "$ROOT/kitty-launcher.conf" \
  --detach \
  "$ROOT/launcher.py"

# Do not rely on pointer position/follow_mouse for initial keyboard focus.
# Retry briefly because the Wayland window may not be mapped on the first tick.
for _ in $(seq 1 20); do
    if hyprctl dispatch focuswindow 'class:^kittyproto-launcher$' >/dev/null 2>&1; then
        exit 0
    fi
    sleep 0.025
done

exit 0
