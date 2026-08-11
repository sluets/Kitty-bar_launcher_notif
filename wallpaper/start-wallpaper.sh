#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Hotkey acts as a toggle.
if pgrep -f -- "$ROOT/wallpaper.py" >/dev/null 2>&1; then
    pkill -TERM -f -- "$ROOT/wallpaper.py" || true
    exit 0
fi

# Read grid geometry without adding another runtime dependency.
read -r COLS ROWS TW TH GX GY PX PY FONT_SIZE < <(
python3 - "$ROOT/wallpaper.toml" <<'PY'
import sys, tomllib
from pathlib import Path
with Path(sys.argv[1]).open('rb') as f:
    c = tomllib.load(f)
w = c.get('wallpaper', {})
a = c.get('appearance', {})
print(
    int(w.get('columns', 5)), int(w.get('rows', 5)),
    int(w.get('thumbnail_width_cells', 18)), int(w.get('thumbnail_height_cells', 9)),
    int(w.get('tile_gap_x_cells', 1)), int(w.get('tile_gap_y_cells', 0)),
    int(w.get('outer_padding_x_cells', 1)), int(w.get('outer_padding_y_cells', 1)),
    float(a.get('font_size', 14.0)),
)
PY
)

# The selector is a graphics overlay, so it consumes no terminal cells.
TILE_W=$TW
TILE_H=$TH
WIN_COLS=$((PX * 2 + COLS * TILE_W + (COLS - 1) * GX))
WIN_LINES=$((PY * 2 + ROWS * TILE_H + (ROWS - 1) * GY))

kitty \
  --class kittyproto-wallpaper \
  --title kittyproto-wallpaper \
  --config "$ROOT/kitty-wallpaper.conf" \
  -o "font_size=$FONT_SIZE" \
  -o "initial_window_width=${WIN_COLS}c" \
  -o "initial_window_height=${WIN_LINES}c" \
  --detach \
  "$ROOT/wallpaper.py"

# Do not rely on pointer position/follow_mouse for initial keyboard focus.
for _ in $(seq 1 20); do
    if hyprctl dispatch focuswindow 'class:^kittyproto-wallpaper$' >/dev/null 2>&1; then
        exit 0
    fi
    sleep 0.025
done

exit 0
