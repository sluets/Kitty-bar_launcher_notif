#!/usr/bin/env python3
from __future__ import annotations

import html
import base64
import json
import os
import re
import select
import shutil
import sys
import termios
import textwrap
import time
import tty
from pathlib import Path

ESC = "\x1b"
CSI = ESC + "["
RESET = CSI + "0m"
BOLD = CSI + "1m"
CLEAR = CSI + "2J" + CSI + "H"
HIDE_CURSOR = CSI + "?25l"
SHOW_CURSOR = CSI + "?25h"
ALT_SCREEN_ON = CSI + "?1049h"
ALT_SCREEN_OFF = CSI + "?1049l"
MOUSE_ON = CSI + "?1000h" + CSI + "?1006h"
MOUSE_OFF = CSI + "?1000l" + CSI + "?1006l"
TAG_RE = re.compile(r"<[^>]+>")


def clean_markup(value: str) -> str:
    value = value.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    value = TAG_RE.sub("", value)
    return html.unescape(value).replace("\r", "")


def rgb(hex_value: str) -> str:
    value = hex_value.lstrip("#")
    try:
        r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except Exception:
        r, g, b = 216, 222, 233
    return f"{CSI}38;2;{r};{g};{b}m"


def fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


def move(row: int, col: int = 1) -> str:
    return f"{CSI}{row};{col}H"


def load_data(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None




def draw_png(path: str, cols: int, rows: int, left_col: int, top_row: int) -> bool:
    p = Path(path)
    if not p.is_file():
        return False
    try:
        # Kitty graphics placements are relative to the current cursor cell.
        # Move there first, then transmit+display a PNG from a local file.
        encoded_path = base64.standard_b64encode(str(p.resolve()).encode('utf-8')).decode('ascii')
        sys.stdout.write(move(top_row, left_col))
        sys.stdout.write(
            f"{ESC}_Ga=T,f=100,t=f,c={cols},r={rows},C=1,q=2;{encoded_path}{ESC}\\"
        )
        return True
    except Exception:
        return False

def render(data: dict) -> None:
    width = max(20, shutil.get_terminal_size((46, 7)).columns)
    height = max(4, shutil.get_terminal_size((46, 7)).lines)
    inner = max(1, width - 4)

    colors = data.get("colors", {})
    fg = rgb(colors.get("foreground", "#d8dee9"))
    accent = rgb(colors.get("accent", "#33ccff"))
    critical = rgb(colors.get("critical", "#ff5577"))

    summary = clean_markup(str(data.get("summary", ""))).replace("\n", " ")
    body = clean_markup(str(data.get("body", "")))
    urgency = int(data.get("urgency", 1))
    body_max_lines = int(data.get("body_max_lines", 3))

    image_path = str(data.get("image_path", "") or "")
    art_width_cols = max(1, int(data.get("art_width_cols", 11)))
    art_height_rows = max(1, int(data.get("art_height_rows", 5)))
    art_x_col = max(1, int(data.get("art_x_col", 3)))
    art_y_row = max(1, int(data.get("art_y_row", 2)))
    art_gap_cols = max(0, int(data.get("art_gap_cols", 2)))
    text_x_offset_cols = int(data.get("text_x_offset_cols", 0))
    text_y_row = max(1, int(data.get("text_y_row", 2)))

    # CSI 2J clears both text and graphics in kitty, so every redraw starts clean.
    sys.stdout.write(CLEAR)

    text_col = max(1, 3 + text_x_offset_cols)
    text_width = max(10, width - text_col - 2)
    if image_path and draw_png(image_path, art_width_cols, art_height_rows, art_x_col, art_y_row):
        text_col = max(1, art_x_col + art_width_cols + art_gap_cols + text_x_offset_cols)
        text_width = max(10, width - text_col - 2)

    lines: list[str] = []
    summary_color = critical if urgency >= 2 else accent
    if summary:
        lines.append(f"{BOLD}{summary_color}{fit(summary, text_width)}{RESET}")
    if body:
        wrapped: list[str] = []
        for para in body.splitlines() or [body]:
            wrapped.extend(textwrap.wrap(para, width=text_width, replace_whitespace=True, drop_whitespace=True) or [""])
        for line in wrapped[:body_max_lines]:
            lines.append(f"{fg}{fit(line, text_width)}{RESET}")

    # Text position is fully configurable from notifs.toml.
    available_rows = max(0, height - text_y_row + 1)
    for idx, line in enumerate(lines[:available_rows], start=text_y_row):
        sys.stdout.write(move(idx, text_col) + line)
    sys.stdout.flush()

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: toast.py SLOT.json", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    last_sig: tuple[int, int] | None = None
    try:
        tty.setcbreak(fd)
        sys.stdout.write(ALT_SCREEN_ON + CLEAR + HIDE_CURSOR + MOUSE_ON)
        sys.stdout.flush()
        buf = ""
        while True:
            try:
                st = path.stat()
                sig = (st.st_mtime_ns, st.st_size)
            except FileNotFoundError:
                return 0
            if sig != last_sig:
                data = load_data(path)
                if data is not None:
                    render(data)
                    last_sig = sig

            ready, _, _ = select.select([sys.stdin], [], [], 0.08)
            if not ready:
                continue
            chunk = os.read(fd, 128).decode("utf-8", "ignore")
            if not chunk:
                return 0
            buf += chunk
            if re.search(r"\x1b\[<\d+;\d+;\d+M", buf):
                return 0
            if "\x1b" in buf or "q" in buf.lower():
                return 0
            if len(buf) > 512:
                buf = buf[-128:]
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(MOUSE_OFF + SHOW_CURSOR + RESET + ALT_SCREEN_OFF)
        sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
