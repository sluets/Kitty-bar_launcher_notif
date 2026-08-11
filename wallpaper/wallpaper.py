#!/usr/bin/env python3
from __future__ import annotations

import os
import random
import base64
import struct
import tempfile
import zlib
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
import tomllib
import tty
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "wallpaper.toml"

ESC = "\x1b"
CSI = ESC + "["
RESET = CSI + "0m"
HIDE_CURSOR = CSI + "?25l"
SHOW_CURSOR = CSI + "?25h"
ALT_SCREEN_ON = CSI + "?1049h"
ALT_SCREEN_OFF = CSI + "?1049l"
CLEAR = CSI + "2J" + CSI + "H"

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
    ".avif", ".jxl",
}

SELECTOR_IMAGE_ID = 424242
SELECTOR_PLACEMENT_ID = 1
THUMB_IMAGE_ID_BASE = 1000
THUMB_PLACEMENT_ID_BASE = 10000
CACHE_LOG = Path(os.path.expanduser("~/.local/state/kittyproto/wallpaper-cache.log"))


def cache_log(message: str) -> None:
    try:
        CACHE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def load_config() -> dict:
    with CONFIG.open("rb") as fh:
        return tomllib.load(fh)


def expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        value = "33ccff"
    try:
        return int(value[:2], 16), int(value[2:4], 16), int(value[4:], 16)
    except ValueError:
        return 51, 204, 255


def discover_thumbnails(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(
        (p for p in path.iterdir() if p.is_file() and p.suffix.casefold() in IMAGE_EXTS),
        key=lambda p: p.name.casefold(),
    )


def resolve_wallpaper(thumb: Path, wallpaper_dir: Path) -> Path | None:
    direct = wallpaper_dir / thumb.name
    if direct.is_file():
        return direct
    matches = [
        p for p in wallpaper_dir.iterdir()
        if p.is_file() and p.suffix.casefold() in IMAGE_EXTS and p.stem == thumb.stem
    ] if wallpaper_dir.is_dir() else []
    if len(matches) == 1:
        return matches[0]
    nested_stem = Path(thumb.stem)
    candidate = wallpaper_dir / nested_stem.name
    if candidate.is_file():
        return candidate
    return None


def current_index(thumbs: list[Path], wallpaper_dir: Path, state_file: Path) -> int:
    try:
        current = Path(state_file.read_text(encoding="utf-8").strip()).expanduser().resolve()
    except OSError:
        return 0
    for i, thumb in enumerate(thumbs):
        target = resolve_wallpaper(thumb, wallpaper_dir)
        if target and target.resolve() == current:
            return i
    return 0


def write_at(row: int, col: int, text: str) -> None:
    sys.stdout.write(f"{CSI}{row + 1};{col + 1}H{text}")
    sys.stdout.flush()


def write_png_border(path: Path, color: str, size: int = 256, border_px: int = 4) -> None:
    r, g, b = rgb(color)
    border_px = max(1, min(border_px, size // 4))
    rows = []
    transparent = bytes((0, 0, 0, 0))
    accent_px = bytes((r, g, b, 255))
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            edge = x < border_px or x >= size - border_px or y < border_px or y >= size - border_px
            row += accent_px if edge else transparent
        rows.append(bytes(row))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)

    raw = b''.join(rows)
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 9))
    png += chunk(b'IEND', b'')
    path.write_bytes(png)


def graphics_command(control: str, payload: bytes = b'') -> None:
    out = sys.stdout.buffer
    out.write(b'\x1b_G' + control.encode('ascii') + b';' + payload + b'\x1b\\')
    out.flush()


def clear_selector() -> None:
    graphics_command(f'a=d,d=I,i={SELECTOR_IMAGE_ID},p={SELECTOR_PLACEMENT_ID},q=1')


def upload_selector(frame_png: Path, x: int, y: int, w: int, h: int) -> None:
    write_at(y, x, '')
    payload = base64.standard_b64encode(str(frame_png).encode('utf-8'))
    graphics_command(
        f'a=T,f=100,t=f,i={SELECTOR_IMAGE_ID},p={SELECTOR_PLACEMENT_ID},c={w},r={h},z=100,q=1,C=1',
        payload,
    )


def move_selector(x: int, y: int, w: int, h: int) -> None:
    write_at(y, x, '')
    graphics_command(f'a=p,i={SELECTOR_IMAGE_ID},p={SELECTOR_PLACEMENT_ID},c={w},r={h},z=100,q=1,C=1')


def delete_visible_all() -> None:
    graphics_command('a=d,d=a,q=1')


def delete_placement(image_id: int, placement_id: int) -> None:
    graphics_command(f'a=d,d=I,i={image_id},p={placement_id},q=1')


def thumb_image_id(index: int) -> int:
    return THUMB_IMAGE_ID_BASE + index


def thumb_placement_id(slot: int) -> int:
    return THUMB_PLACEMENT_ID_BASE + slot


def upload_page_image(path: Path, image_id: int, placement_id: int, x: int, y: int, w: int, h: int) -> None:
    """Transmit and display one normalized PNG for the current page.

    This intentionally does not use the speculative background cache. The old
    picker delay came mostly from spawning `kitten icat` once per thumbnail.
    With the .thumbs directory normalized to PNG, direct protocol transmission
    from this single Python process is cheap and deterministic.
    """
    write_at(y, x, '')
    payload = base64.standard_b64encode(str(path).encode('utf-8'))
    cache_log(f'upload+place image_id={image_id} placement_id={placement_id} path={path.name}')
    graphics_command(
        f'a=T,f=100,t=f,i={image_id},p={placement_id},c={w},r={h},z=1,q=1,C=1',
        payload,
    )


def read_key(fd: int) -> str:
    """Return one real keyboard key, discarding Kitty graphics replies.

    Graphics replies use APC sequences:
        ESC _ G ... ESC \

    With q=1, successful graphics replies are suppressed but errors are still
    delivered to the TTY. If we return only ESC_ and leave the rest buffered,
    words/spaces from an ENOENT message get interpreted as user keystrokes.
    A literal space then triggers wallpaper selection. Consume the *entire*
    graphics reply here and continue waiting for an actual key.
    """
    while True:
        first = os.read(fd, 1)
        if not first:
            return ""
        if first != b"\x1b":
            return first.decode("utf-8", "ignore")

        ready, _, _ = select.select([fd], [], [], 0.025)
        if not ready:
            return "ESC"

        second = os.read(fd, 1)

        # Kitty graphics protocol reply: ESC _ G ... ESC \
        if second == b"_":
            payload = bytearray()
            prev_esc = False
            while len(payload) < 8192:
                byte = os.read(fd, 1)
                if not byte:
                    break
                if prev_esc:
                    if byte == b"\\":
                        break
                    payload += b"\x1b"
                    prev_esc = False
                if byte == b"\x1b":
                    prev_esc = True
                else:
                    payload += byte

            try:
                message = payload.decode("utf-8", "replace")
                if message.startswith("G"):
                    cache_log(f"kitty-reply {message[1:]}")
                else:
                    cache_log(f"terminal-apc {message}")
            except Exception:
                pass

            # This was terminal protocol traffic, not a key. Read again.
            continue

        seq = bytearray(first + second)

        # CSI / SS3 keyboard sequence.
        if second in (b"[", b"O"):
            while len(seq) < 32:
                ready, _, _ = select.select([fd], [], [], 0.025)
                if not ready:
                    break
                byte = os.read(fd, 1)
                if not byte:
                    break
                seq += byte
                value = byte[0]
                if 0x40 <= value <= 0x7E:
                    break

        return bytes(seq).decode("utf-8", "ignore")


def apply_wallpaper(path: Path, state_file: Path, transition_type: str, duration: float, fps: int) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(state_file.suffix + ".tmp")
    tmp.write_text(str(path) + "\n", encoding="utf-8")
    tmp.replace(state_file)
    subprocess.Popen(
        [
            "awww", "img", str(path),
            "--transition-type", transition_type,
            "--transition-duration", str(duration),
            "--transition-fps", str(fps),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main() -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("kittyproto wallpaper picker requires a TTY", file=sys.stderr)
        return 1
    if shutil.which("kitten") is None:
        print("kittyproto wallpaper picker: kitten not found", file=sys.stderr)
        return 1
    if shutil.which("awww") is None:
        print("kittyproto wallpaper picker: awww not found", file=sys.stderr)
        return 1

    cfg = load_config()
    try:
        CACHE_LOG.parent.mkdir(parents=True, exist_ok=True)
        CACHE_LOG.write_text("", encoding="utf-8")
    except OSError:
        pass
    wc = cfg.get("wallpaper", {})
    ac = cfg.get("appearance", {})

    wallpaper_dir = expand(str(wc.get("wallpaper_dir", "~/Pictures/Wallpapers")))
    thumb_dir = expand(str(wc.get("thumbnail_dir", "~/Pictures/Wallpapers/.thumbs")))
    state_file = expand(str(wc.get("state_file", "~/.config/awww/current-wallpaper")))

    cols = max(1, int(wc.get("columns", 5)))
    rows = max(1, int(wc.get("rows", 5)))
    page_size = cols * rows
    iw = max(2, int(wc.get("thumbnail_width_cells", 18)))
    ih = max(1, int(wc.get("thumbnail_height_cells", 9)))
    gx = max(-4, int(wc.get("tile_gap_x_cells", 0)))
    gy = max(-4, int(wc.get("tile_gap_y_cells", 0)))
    px = max(0, int(wc.get("outer_padding_x_cells", 0)))
    py = max(0, int(wc.get("outer_padding_y_cells", 0)))
    accent = str(ac.get("accent", "#33ccff"))

    selector_border = max(1, int(ac.get("selector_border_source_px", 4)))
    selector_tmp = Path(tempfile.gettempdir()) / f"kittyproto-wallpaper-selector-{os.getpid()}.png"
    write_png_border(selector_tmp, accent, size=256, border_px=selector_border)

    transition_type = str(wc.get("transition_type", "wipe"))
    duration = max(0.0, float(wc.get("transition_duration", 2.0)))
    fps = max(1, int(wc.get("transition_fps", 60)))

    thumbs = discover_thumbnails(thumb_dir)
    if bool(wc.get("random_order", False)):
        random.shuffle(thumbs)
    if not thumbs:
        print(f"No thumbnails found in {thumb_dir}")
        time.sleep(1.5)
        return 1

    selected = min(current_index(thumbs, wallpaper_dir, state_file), len(thumbs) - 1)
    tile_w = iw
    tile_h = ih
    old_term = termios.tcgetattr(sys.stdin.fileno())
    running = True
    visible_placements: list[tuple[int, int]] = []

    def cleanup(*_args) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    def tile_xy(slot: int) -> tuple[int, int]:
        r, c = divmod(slot, cols)
        return px + c * (tile_w + gx), py + r * (tile_h + gy)

    def current_page_bounds() -> tuple[int, int]:
        page = selected // page_size
        start = page * page_size
        end = min(len(thumbs), start + page_size)
        return start, end

    def render_page() -> None:
        nonlocal visible_placements
        start, end = current_page_bounds()
        for image_id, placement_id in visible_placements:
            delete_placement(image_id, placement_id)
        visible_placements = []
        sys.stdout.write(CLEAR)
        sys.stdout.flush()
        for slot, idx in enumerate(range(start, end)):
            x, y = tile_xy(slot)
            image_id = thumb_image_id(idx)
            placement_id = thumb_placement_id(slot)
            upload_page_image(thumbs[idx], image_id, placement_id, x, y, iw, ih)
            visible_placements.append((image_id, placement_id))
        slot = selected - start
        x, y = tile_xy(slot)
        upload_selector(selector_tmp, x, y, tile_w, tile_h)
        sys.stdout.flush()

    try:
        tty.setraw(sys.stdin.fileno())
        sys.stdout.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR)
        sys.stdout.flush()
        render_page()

        fd = sys.stdin.fileno()
        while running:
            key = read_key(fd)
            if key in ("ESC", "q", "Q", "\x03"):
                break

            old_selected = selected
            old_page = selected // page_size

            if key in ("\r", "\n", " "):
                target = resolve_wallpaper(thumbs[selected], wallpaper_dir)
                if target is not None:
                    apply_wallpaper(target, state_file, transition_type, duration, fps)
                    break
                slot = selected % page_size
                x, y = tile_xy(slot)
                clear_selector()
                sys.stdout.flush()
                time.sleep(0.08)
                upload_selector(selector_tmp, x, y, tile_w, tile_h)
                sys.stdout.flush()
                continue

            if key in (CSI + "A", "k", "K"):
                selected = max(0, selected - cols)
            elif key in (CSI + "B", "j", "J"):
                selected = min(len(thumbs) - 1, selected + cols)
            elif key in (CSI + "D", "h", "H"):
                selected = max(0, selected - 1)
            elif key in (CSI + "C", "l", "L"):
                selected = min(len(thumbs) - 1, selected + 1)
            elif key in (CSI + "5~",):
                selected = max(0, selected - page_size)
            elif key in (CSI + "6~",):
                selected = min(len(thumbs) - 1, selected + page_size)
            elif key in (CSI + "H", CSI + "1~", CSI + "7~"):
                selected = 0
            elif key in (CSI + "F", CSI + "4~", CSI + "8~"):
                selected = len(thumbs) - 1
            else:
                continue

            if selected == old_selected:
                continue

            new_page = selected // page_size
            if new_page != old_page:
                render_page()
                continue

            new_slot = selected % page_size
            nx, ny = tile_xy(new_slot)
            move_selector(nx, ny, tile_w, tile_h)
            sys.stdout.flush()
    finally:
        clear_selector()
        for image_id, placement_id in visible_placements:
            delete_placement(image_id, placement_id)
        delete_visible_all()
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_term)
        sys.stdout.write(RESET + SHOW_CURSOR + ALT_SCREEN_OFF)
        sys.stdout.flush()
        try:
            selector_tmp.unlink()
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
