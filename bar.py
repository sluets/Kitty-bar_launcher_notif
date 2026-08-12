#!/usr/bin/env python3
"""kitty-desktop Rev 4.2 bar: event-driven MPD media, audio, workspaces, Wi-Fi/Bluetooth, clock."""

from __future__ import annotations

import json
import math
import struct
import tempfile
import termios
import tty
import zlib
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError as exc:
    raise SystemExit("kitty-desktop requires Python 3.11+ (tomllib)") from exc

ROOT = Path(__file__).resolve().parent
THEME_FILE = Path(os.environ.get("KITTY_DESKTOP_THEME", ROOT / "theme.toml"))
RUNNING = True
RESET = "\x1b[0m"


def load_config() -> dict:
    with THEME_FILE.open("rb") as fh:
        return tomllib.load(fh)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected #RRGGBB, got {value!r}")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def fg(value: str) -> str:
    r, g, b = hex_to_rgb(value)
    return f"\x1b[38;2;{r};{g};{b}m"


def bg(value: str) -> str:
    r, g, b = hex_to_rgb(value)
    return f"\x1b[48;2;{r};{g};{b}m"



def rgba_tuple(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    r, g, b = hex_to_rgb(value)
    return r, g, b, max(0, min(255, alpha))


def png_chunk(kind: bytes, data: bytes) -> bytes:
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)


def write_rgba_png(path: Path, width: int, height: int, pixels: bytes) -> None:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # PNG filter: None
        start = y * stride
        raw.extend(pixels[start:start + stride])
    data = (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + png_chunk(b"IEND", b"")
    )
    path.write_bytes(data)


def mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def gradient_color(stops: list[tuple[int, int, int]], t: float) -> tuple[int, int, int]:
    if not stops:
        return 255, 255, 255
    if len(stops) == 1:
        return stops[0]
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    i = min(len(stops) - 2, int(t))
    return mix(stops[i], stops[i + 1], t - i)


def rounded_inside(x: float, y: float, w: float, h: float, radius: float) -> bool:
    if radius <= 0:
        return 0 <= x < w and 0 <= y < h
    r = min(radius, w / 2.0, h / 2.0)
    cx = r if x < r else (w - r if x >= w - r else x)
    cy = r if y < r else (h - r if y >= h - r else y)
    if x == cx or y == cy:
        return 0 <= x < w and 0 <= y < h
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= r * r


def make_chrome_png(path: Path, width: int, height: int) -> None:
    colors = [c for c in os.environ.get("KITTY_DESKTOP_BORDER_COLORS", "#33ccff").split(",") if c]
    stops = [hex_to_rgb(c) for c in colors]
    angle = math.radians(float(os.environ.get("KITTY_DESKTOP_BORDER_ANGLE", "45")))
    border = max(0.0, float(os.environ.get("KITTY_DESKTOP_BORDER_WIDTH", "2")))
    radius = max(0.0, float(os.environ.get("KITTY_DESKTOP_RADIUS", "12")))
    bg_rgb = hex_to_rgb(os.environ.get("KITTY_DESKTOP_BACKGROUND", "#101318"))
    bg_alpha = round(255 * max(0.0, min(1.0, float(os.environ.get("KITTY_DESKTOP_BACKGROUND_OPACITY", "0.88")))))

    # 2x supersampling keeps 1-2 px rounded borders reasonably smooth without
    # adding Pillow/ImageMagick as a project dependency.
    ss = 2
    sw, sh = width * ss, height * ss
    sradius, sborder = radius * ss, border * ss
    dx, dy = math.cos(angle), math.sin(angle)
    corners = [(0.0, 0.0), (sw, 0.0), (0.0, sh), (sw, sh)]
    projections = [x * dx + y * dy for x, y in corners]
    pmin, pmax = min(projections), max(projections)
    span = max(1.0, pmax - pmin)

    hi = bytearray(sw * sh * 4)
    inner_w = max(0.0, sw - 2 * sborder)
    inner_h = max(0.0, sh - 2 * sborder)
    inner_r = max(0.0, sradius - sborder)

    for y in range(sh):
        fy = y + 0.5
        for x in range(sw):
            fx = x + 0.5
            if not rounded_inside(fx, fy, sw, sh, sradius):
                continue

            inside_inner = False
            if inner_w > 0 and inner_h > 0:
                inside_inner = rounded_inside(fx - sborder, fy - sborder, inner_w, inner_h, inner_r)

            pos = (y * sw + x) * 4
            if inside_inner:
                hi[pos:pos + 4] = bytes((*bg_rgb, bg_alpha))
            else:
                t = ((fx * dx + fy * dy) - pmin) / span
                hi[pos:pos + 4] = bytes((*gradient_color(stops, t), 255))

    # Box-filter downsample into the actual panel pixel size.
    out = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            sums = [0, 0, 0, 0]
            for oy in range(ss):
                for ox in range(ss):
                    pos = (((y * ss + oy) * sw) + (x * ss + ox)) * 4
                    for c in range(4):
                        sums[c] += hi[pos + c]
            dst = (y * width + x) * 4
            out[dst:dst + 4] = bytes(round(v / (ss * ss)) for v in sums)

    write_rgba_png(path, width, height, bytes(out))


def terminal_window_pixels() -> tuple[int, int] | None:
    if shutil.which("kitten") is None:
        return None
    try:
        proc = subprocess.run(
            ["kitten", "icat", "--print-window-size"],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=True,
        )
        match = re.search(r"(\d+)x(\d+)", proc.stdout + proc.stderr)
        if match:
            return int(match.group(1)), int(match.group(2))
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def stable_terminal_window_pixels(expected_height: int, timeout: float = 2.5) -> tuple[int, int] | None:
    """Wait for kitty's panel surface to reach a sane, stable pixel size.

    During compositor startup / output hotplug, kitty can briefly report a tiny
    positive window height. A 1-4 px-high image makes a normal 2 px border fill
    almost the entire chrome PNG, which is then stretched across the settled
    bar and appears as a solid gradient.

    Require a plausible height relative to [bar].height_px and two consecutive
    identical readings before using the geometry.
    """
    deadline = time.monotonic() + max(0.1, timeout)
    min_height = max(12, round(expected_height * 0.70))
    last: tuple[int, int] | None = None
    stable_count = 0

    while RUNNING and time.monotonic() < deadline:
        size = terminal_window_pixels()

        if size is not None:
            width, height = size
            sane = width >= 100 and height >= min_height

            if sane:
                if size == last:
                    stable_count += 1
                else:
                    last = size
                    stable_count = 1

                if stable_count >= 2:
                    return size
            else:
                last = None
                stable_count = 0

        time.sleep(0.08)

    # If the compositor is still settling, do not draw bad chrome. The normal
    # transparent kitty surface is preferable to painting a bogus full-gradient
    # image. A later retry can draw once geometry is valid.
    return None


def draw_chrome(cfg: dict, *, wait_for_stable: bool = False) -> bool:
    if os.environ.get("KITTY_DESKTOP_CHROME_MODE") == "background-image":
        return True

    expected_height = max(16, int(cfg.get("bar", {}).get("height_px", 34)))

    if wait_for_stable:
        size = stable_terminal_window_pixels(expected_height)
    else:
        size = terminal_window_pixels()
        if size is not None:
            width, height = size
            if width < 100 or height < max(12, round(expected_height * 0.70)):
                size = None

    if size is None:
        return False

    width, height = size
    cols, rows = shutil.get_terminal_size((120, 1))
    if width <= 0 or height <= 0 or cols <= 0 or rows <= 0:
        return False

    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir()))
    path = runtime / f"kitty-desktop-chrome-{os.getpid()}.png"
    try:
        make_chrome_png(path, width, height)
        # Very negative z-index puts the image below text and below explicit
        # cell backgrounds (the active workspace pill can still sit on top).
        subprocess.run(
            [
                "kitten", "icat",
                "--stdin=no",
                f"--use-window-size={cols},{rows},{width},{height}",
                f"--place={cols}x{rows}@0x0",
                "--align=left",
                "--scale-up",
                "--transfer-mode=stream",
                "--z-index=-1073741825",
                str(path),
            ],
            stdout=sys.stdout,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            check=False,
        )
        sys.stdout.flush()
        return True
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def hypr_json(*args: str):
    try:
        proc = subprocess.run(
            ["hyprctl", "-j", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=0.75,
            check=True,
        )
        return json.loads(proc.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return None


def query_workspace_state() -> tuple[list[int], int | None]:
    workspaces = hypr_json("workspaces")
    active = hypr_json("activeworkspace")

    ids: list[int] = []
    if isinstance(workspaces, list):
        ids = sorted(
            ws["id"]
            for ws in workspaces
            if isinstance(ws, dict)
            and isinstance(ws.get("id"), int)
            and ws["id"] > 0
        )

    active_id = active.get("id") if isinstance(active, dict) else None
    if isinstance(active_id, int) and active_id > 0 and active_id not in ids:
        ids.append(active_id)
        ids.sort()

    return ids, active_id if isinstance(active_id, int) else None


def visible_workspace_ids(existing: list[int], active: int | None, minimum: int) -> list[int]:
    ids = set(range(1, max(1, minimum) + 1))
    ids.update(existing)
    if active and active > 0:
        ids.add(active)
    return sorted(i for i in ids if i > 0)


def run_text(command: list[str], timeout: float = 0.7) -> str:
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""



def mpd_socket_path(cfg: dict) -> Path:
    media = cfg.get("media", {})
    configured = str(media.get("socket", "")).strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "mpd" / "socket"
    return Path(f"/run/user/{os.getuid()}/mpd/socket")


def _mpd_read_response(sock: socket.socket, timeout: float = 0.7) -> list[str] | None:
    sock.settimeout(timeout)
    data = bytearray()
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            data.extend(chunk)
            if data.endswith(b"OK\n") or data.startswith(b"ACK ") or b"\nACK " in data:
                break
    except (OSError, socket.timeout):
        return None

    lines = data.decode("utf-8", "replace").splitlines()
    if any(line.startswith("ACK ") for line in lines):
        return None
    return [line for line in lines if line != "OK"]


def _mpd_connect(cfg: dict, timeout: float = 0.7) -> socket.socket | None:
    path = mpd_socket_path(cfg)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        greeting = bytearray()
        while b"\n" not in greeting:
            chunk = sock.recv(256)
            if not chunk:
                raise OSError("MPD closed before greeting")
            greeting.extend(chunk)
        if not greeting.decode("utf-8", "replace").startswith("OK MPD "):
            raise OSError("invalid MPD greeting")
        return sock
    except OSError:
        sock.close()
        return None


def mpd_command(cfg: dict, command: str, timeout: float = 0.7) -> list[str] | None:
    sock = _mpd_connect(cfg, timeout=timeout)
    if sock is None:
        return None
    try:
        sock.sendall((command + "\n").encode())
        return _mpd_read_response(sock, timeout=timeout)
    except OSError:
        return None
    finally:
        sock.close()


def _mpd_fields(lines: list[str] | None) -> dict[str, str]:
    fields: dict[str, str] = {}
    if not lines:
        return fields
    for line in lines:
        key, sep, value = line.partition(": ")
        if sep and key not in fields:
            fields[key] = value
    return fields


class MPDIdleSource:
    """One long-lived MPD connection blocked in `idle player`.

    The main loop selects on this socket. MPD wakes it immediately when
    playback state or the current song changes. Queries/actions use separate
    short-lived connections so this watcher can remain blocked in `idle`.
    """

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.sock: socket.socket | None = None
        self.buffer = bytearray()
        self.next_retry = 0.0
        self.retry_seconds = max(
            0.5, float(cfg.get("media", {}).get("reconnect_seconds", 2.0))
        )
        self.connect()

    def connect(self) -> None:
        now = time.monotonic()
        if self.sock is not None or now < self.next_retry:
            return
        sock = _mpd_connect(self.cfg)
        if sock is None:
            self.next_retry = now + self.retry_seconds
            return
        try:
            sock.setblocking(False)
            sock.sendall(b"idle player\n")
        except OSError:
            sock.close()
            self.next_retry = now + self.retry_seconds
            return
        self.sock = sock
        self.buffer.clear()

    def fileno(self) -> int | None:
        if self.sock is None:
            return None
        try:
            return self.sock.fileno()
        except OSError:
            return None

    def drain(self) -> bool:
        """Consume one idle response; return True when player state changed."""
        sock = self.sock
        if sock is None:
            return False

        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    self.close()
                    return False
                self.buffer.extend(chunk)
                if len(chunk) < 4096:
                    break
        except BlockingIOError:
            pass
        except OSError:
            self.close()
            return False

        raw = bytes(self.buffer)
        if b"\nOK\n" not in raw and not raw.endswith(b"OK\n") and b"\nACK " not in raw:
            return False

        lines = raw.decode("utf-8", "replace").splitlines()
        changed = any(line == "changed: player" for line in lines)
        self.buffer.clear()

        try:
            sock.sendall(b"idle player\n")
        except OSError:
            self.close()

        return changed

    def restart_if_needed(self) -> bool:
        """Reconnect if needed; True means caller should refresh media state."""
        if self.sock is not None:
            return False
        before = self.sock
        self.connect()
        return before is None and self.sock is not None

    def close(self) -> None:
        sock = self.sock
        self.sock = None
        self.buffer.clear()
        self.next_retry = time.monotonic() + self.retry_seconds
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def query_media(cfg: dict) -> tuple[str, str, str] | None:
    media = cfg.get("media", {})
    if not bool(media.get("enabled", True)):
        return None

    backend = str(media.get("backend", "mpd")).strip().lower()
    if backend == "mpd":
        status_fields = _mpd_fields(mpd_command(cfg, "status"))
        song_fields = _mpd_fields(mpd_command(cfg, "currentsong"))
        if not status_fields or not song_fields:
            return None

        state = status_fields.get("state", "")
        artist = song_fields.get("Artist", "").strip()
        title = song_fields.get("Title", "").strip()
        if not title:
            title = Path(song_fields.get("file", "")).stem
        if not artist and not title:
            return None
        return state, artist, title

    if backend == "mpris":
        if shutil.which("playerctl") is None:
            return None
        player = str(media.get("player", "")).strip()
        command = ["playerctl"]
        if player:
            command.append(f"--player={player}")
        output = run_text(command + ["metadata", "--format", "{{artist}}\t{{title}}"])
        if not output:
            return None
        status = run_text(command + ["status"])
        parts = output.split("\t", 1)
        while len(parts) < 2:
            parts.append("")
        artist, title = (part.strip() for part in parts[:2])
        if not artist and not title:
            return None
        return status, artist, title

    return None

def query_volume(cfg: dict) -> tuple[int, bool] | None:
    volume = cfg.get("volume", {})
    if not bool(volume.get("enabled", True)) or shutil.which("wpctl") is None:
        return None

    target = str(volume.get("target", "@DEFAULT_AUDIO_SINK@")).strip() or "@DEFAULT_AUDIO_SINK@"
    output = run_text(["wpctl", "get-volume", target])
    if not output:
        return None
    match = re.search(r"Volume:\s*([0-9]+(?:\.[0-9]+)?)", output, re.I)
    if not match:
        return None
    percent = max(0, round(float(match.group(1)) * 100))
    muted = "MUTED" in output.upper()
    return percent, muted


def query_wifi(cfg: dict) -> tuple[bool, bool] | None:
    network = cfg.get("network", {})
    if not bool(network.get("enabled", True)) or shutil.which("nmcli") is None:
        return None

    radio = run_text(["nmcli", "radio", "wifi"])
    powered = radio.strip().lower() == "enabled"
    if not powered:
        return False, False

    devices = run_text(["nmcli", "-t", "-f", "TYPE,STATE", "device", "status"])
    connected = any(
        line.lower().startswith("wifi:connected")
        for line in devices.splitlines()
    )
    return True, connected


def query_bluetooth(cfg: dict) -> bool | None:
    bluetooth = cfg.get("bluetooth", {})
    if not bool(bluetooth.get("enabled", True)) or shutil.which("bluetoothctl") is None:
        return None
    output = run_text(["bluetoothctl", "show"], timeout=1.0)
    if not output:
        return None
    match = re.search(r"^\s*Powered:\s*(yes|no)\s*$", output, re.I | re.M)
    return bool(match and match.group(1).lower() == "yes")


def spawn(command: list[str]) -> None:
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def dispatch_workspace(workspace_id: int) -> None:
    spawn(["hyprctl", "dispatch", "workspace", str(workspace_id)])


def media_action(cfg: dict, action: str) -> None:
    media = cfg.get("media", {})
    backend = str(media.get("backend", "mpd")).strip().lower()

    if backend == "mpd":
        command = {
            "play-pause": "pause",
            "next": "next",
            "previous": "previous",
        }.get(action)
        if command:
            mpd_command(cfg, command)
        return

    if backend == "mpris" and shutil.which("playerctl") is not None:
        player = str(media.get("player", "")).strip()
        command = ["playerctl"]
        if player:
            command.append(f"--player={player}")
        command.append(action)
        spawn(command)

def volume_action(cfg: dict, action: str) -> None:
    volume = cfg.get("volume", {})
    target = str(volume.get("target", "@DEFAULT_AUDIO_SINK@")).strip() or "@DEFAULT_AUDIO_SINK@"
    if action == "open":
        if shutil.which("pavucontrol"):
            spawn(["pavucontrol"])
    elif action == "mute" and shutil.which("wpctl"):
        spawn(["wpctl", "set-mute", target, "toggle"])
    elif action == "up" and shutil.which("wpctl"):
        step = max(1, int(volume.get("scroll_step_percent", 5)))
        spawn(["wpctl", "set-volume", "-l", "1.5", target, f"{step}%+"])
    elif action == "down" and shutil.which("wpctl"):
        step = max(1, int(volume.get("scroll_step_percent", 5)))
        spawn(["wpctl", "set-volume", target, f"{step}%-"])


def open_network(cfg: dict) -> None:
    command = cfg.get("network", {}).get("click_command", ["kitty", "nmtui"])
    if isinstance(command, list) and command:
        spawn([str(x) for x in command])


def open_bluetooth(cfg: dict) -> None:
    command = cfg.get("bluetooth", {}).get("click_command", ["blueman-manager"])
    if isinstance(command, list) and command:
        spawn([str(x) for x in command])


class AudioEventSource:
    """Wake the bar when PipeWire/Pulse audio state changes.

    `pactl subscribe` is preferred because it emits compact newline-delimited
    events and works with pipewire-pulse. `pw-mon` is a direct PipeWire fallback.
    The event stream is only a wake-up signal: wpctl remains the single source
    of truth for the displayed default-sink volume/mute state.
    """

    def __init__(self) -> None:
        self.proc: subprocess.Popen[str] | None = None
        self.backend = ""
        self._start()

    def _start(self) -> None:
        commands: list[tuple[str, list[str]]] = []
        if shutil.which("pactl"):
            commands.append(("pactl", ["pactl", "subscribe"]))
        if shutil.which("pw-mon"):
            # -m disables ANSI color, which keeps output parsing harmless.
            commands.append(("pw-mon", ["pw-mon", "-m"]))

        for backend, command in commands:
            try:
                self.proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                if self.proc.stdout is None:
                    self.proc.terminate()
                    self.proc = None
                    continue
                os.set_blocking(self.proc.stdout.fileno(), False)
                self.backend = backend
                return
            except OSError:
                self.proc = None
        self.backend = ""

    def fileno(self) -> int | None:
        if self.proc is None or self.proc.stdout is None:
            return None
        if self.proc.poll() is not None:
            return None
        return self.proc.stdout.fileno()

    def drain(self) -> bool:
        """Consume pending events and return True when volume should refresh."""
        if self.proc is None or self.proc.stdout is None:
            return False
        changed = False
        try:
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    break
                if self.backend == "pactl":
                    lower = line.lower()
                    # Default-sink changes arrive as server events; sink events
                    # cover volume/mute/device updates. Sink-input churn cannot
                    # affect the displayed master volume, so ignore it.
                    if "on sink " in lower or "on server" in lower:
                        changed = True
                else:
                    # pw-mon is intentionally treated as a wake-up stream. It
                    # can be verbose, but every burst is coalesced into one
                    # wpctl read and one render below.
                    changed = True
        except (BlockingIOError, OSError):
            pass
        return changed

    def restart_if_dead(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        self.close()
        self._start()

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=0.25)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
            except OSError:
                pass


def truncate_cells(text: str, max_cells: int) -> str:
    if max_cells <= 0:
        return ""
    if len(text) <= max_cells:
        return text
    if max_cells == 1:
        return "…"
    return text[: max_cells - 1] + "…"


def media_text(cfg: dict, state: tuple[str, str, str] | None) -> str:
    if state is None:
        return ""
    _status, artist, title = state
    media = cfg.get("media", {})
    if artist and title:
        body = f"{artist} — {title}"
    else:
        body = artist or title
    max_length = max(0, int(media.get("max_length", 64)))
    return truncate_cells(body, max_length)


def volume_text(cfg: dict, state: tuple[int, bool] | None) -> str:
    if state is None:
        return ""
    percent, muted = state
    volume = cfg.get("volume", {})
    if muted:
        icon = str(volume.get("muted_icon", "󰖁"))
    elif percent >= 67:
        icon = str(volume.get("high_icon", "󰕾"))
    elif percent >= 34:
        icon = str(volume.get("medium_icon", "󰖀"))
    else:
        icon = str(volume.get("low_icon", "󰕿"))
    return f"{icon} {percent}%"


def wifi_text(cfg: dict, state: tuple[bool, bool] | None) -> str:
    if state is None:
        return ""
    powered, connected = state
    network = cfg.get("network", {})
    if not powered:
        return str(network.get("off_icon", "󰤭"))
    return str(network.get("connected_icon", "󰤨") if connected else network.get("disconnected_icon", "󰤯"))


def bluetooth_text(cfg: dict, powered: bool | None) -> str:
    if powered is None:
        return ""
    bluetooth = cfg.get("bluetooth", {})
    return str(bluetooth.get("on_icon", "󰂯") if powered else bluetooth.get("off_icon", "󰂲"))


def render(cfg: dict, existing: list[int], active: int | None,
           media_state: tuple[str, str, str] | None = None,
           volume_state: tuple[int, bool] | None = None,
           wifi_state: tuple[bool, bool] | None = None,
           bluetooth_state: bool | None = None) -> list[tuple[int, int, str, int | None]]:
    bar = cfg.get("bar", {})
    text = cfg.get("text", {})
    workspaces = cfg.get("workspaces", {})
    media_cfg = cfg.get("media", {})
    volume_cfg = cfg.get("volume", {})
    status_cfg = cfg.get("status", {})
    network_cfg = cfg.get("network", {})
    bluetooth_cfg = cfg.get("bluetooth", {})

    cols = shutil.get_terminal_size((120, 1)).columns
    minimum = int(workspaces.get("minimum", 5))
    ids = visible_workspace_ids(existing, active, minimum)

    # Visible workspace padding and the gap between workspace cells are separate.
    # This lets the numbers sit tightly while preserving a readable separator.
    workspace_pad_n = max(0, int(workspaces.get("padding_cells", 0)))
    workspace_gap_n = max(0, int(workspaces.get("gap_cells", 1)))
    workspace_pad = " " * workspace_pad_n
    workspace_gap = " " * workspace_gap_n

    active_bg = str(workspaces.get("active_background", "#33ccff"))
    if bool(workspaces.get("use_border_color_for_active", True)):
        active_bg = os.environ.get("KITTY_DESKTOP_BORDER_COLOR", active_bg)

    active_fg = str(workspaces.get("active_foreground", "#101318"))
    inactive_fg = str(workspaces.get("inactive_foreground", "#c7ccd6"))
    foreground = str(text.get("foreground", "#d8dee9"))
    muted_fg = str(text.get("muted", "#687080"))

    left_prefix = str(bar.get("left_prefix", "   "))
    right_padding = " " * max(0, int(bar.get("right_padding_cells", 2)))

    display_mode = str(workspaces.get("display", "numbers")).strip().lower()
    if display_mode not in {"numbers", "circles", "squares"}:
        display_mode = "numbers"

    def workspace_label(wid: int, is_active: bool) -> str:
        if display_mode == "circles":
            key = "circle_active" if is_active else "circle_inactive"
            fallback = "●" if is_active else "○"
            return str(workspaces.get(key, fallback))
        if display_mode == "squares":
            key = "square_active" if is_active else "square_inactive"
            fallback = "■" if is_active else "□"
            return str(workspaces.get(key, fallback))
        return str(wid)

    chunks: list[str] = []
    plain_chunks: list[str] = []
    hit_regions: list[tuple[int, int, str, int | None]] = []
    cursor_col = len(left_prefix) + 1
    for index, wid in enumerate(ids):
        label = workspace_label(wid, wid == active)
        plain = f"{workspace_pad}{label}{workspace_pad}"
        plain_chunks.append(plain)
        start_col = cursor_col
        end_col = cursor_col + len(plain) - 1
        hit_regions.append((start_col, end_col, "workspace", wid))
        cursor_col = end_col + 1
        if wid == active:
            chunks.append(f"{bg(active_bg)}{fg(active_fg)}{plain}{RESET}")
        else:
            chunks.append(f"{fg(inactive_fg)}{plain}{RESET}")
        if index < len(ids) - 1:
            cursor_col += workspace_gap_n

    workspace_ansi = fg(foreground) + left_prefix + RESET + workspace_gap.join(chunks)
    workspace_plain = left_prefix + workspace_gap.join(plain_chunks)

    clock_plain = datetime.now().strftime(str(bar.get("clock_format", "%I:%M:%S %p")))
    vol_plain = volume_text(cfg, volume_state)
    wifi_plain = wifi_text(cfg, wifi_state)
    bt_plain = bluetooth_text(cfg, bluetooth_state)

    # Click padding is real blank terminal space owned by the module. That gives
    # tiny glyphs (Wi-Fi/Bluetooth) a forgiving hitbox without requiring large
    # visual separators between modules.
    status_gap_n = max(0, int(status_cfg.get("gap_cells", 0)))
    clock_gap_n = max(0, int(status_cfg.get("clock_gap_cells", 1)))
    status_gap = " " * status_gap_n
    clock_gap = " " * clock_gap_n

    status_parts: list[tuple[str, str, str, int]] = []
    if vol_plain:
        click_pad = max(0, int(volume_cfg.get("click_padding_cells", 1)))
        padded = (" " * click_pad) + vol_plain + (" " * click_pad)
        status_parts.append(("volume", vol_plain, padded, click_pad))
    if wifi_plain:
        click_pad = max(0, int(network_cfg.get("click_padding_cells", 1)))
        padded = (" " * click_pad) + wifi_plain + (" " * click_pad)
        status_parts.append(("network", wifi_plain, padded, click_pad))
    if bt_plain:
        click_pad = max(0, int(bluetooth_cfg.get("click_padding_cells", 1)))
        padded = (" " * click_pad) + bt_plain + (" " * click_pad)
        status_parts.append(("bluetooth", bt_plain, padded, click_pad))
    status_parts.append(("clock", clock_plain, clock_plain, 0))

    right_plain_parts: list[str] = []
    for index, (kind, _visible, padded, _click_pad) in enumerate(status_parts):
        if index:
            previous_kind = status_parts[index - 1][0]
            right_plain_parts.append(clock_gap if kind == "clock" or previous_kind == "clock" else status_gap)
        right_plain_parts.append(padded)
    right_plain = "".join(right_plain_parts) + right_padding
    right_start = max(1, cols - len(right_plain) + 1)

    # Now-playing belongs to the left cluster, directly after workspaces.
    media_plain = media_text(cfg, media_state)
    media_gap_n = max(0, int(media_cfg.get("gap_cells", 2)))
    media_gap = " " * media_gap_n
    media_click_pad = max(0, int(media_cfg.get("click_padding_cells", 0)))
    media_color = str(media_cfg.get("foreground", foreground))
    media_available = max(0, right_start - len(workspace_plain) - 2)
    if media_plain:
        reserved = media_gap_n + (media_click_pad * 2)
        if media_available <= reserved:
            media_plain = ""
        else:
            media_plain = truncate_cells(media_plain, media_available - reserved)

    out = ["\r\x1b[2K", workspace_ansi]
    if media_plain:
        media_block = (" " * media_click_pad) + media_plain + (" " * media_click_pad)
        media_start = len(workspace_plain) + media_gap_n + 1
        media_end = media_start + len(media_block) - 1
        hit_regions.append((media_start, media_end, "media", None))
        out += [fg(media_color), media_gap, media_block, RESET]

    out += [f"\x1b[{right_start}G"]
    right_cursor = right_start
    for index, (kind, visible_part, padded_part, click_pad) in enumerate(status_parts):
        if index:
            previous_kind = status_parts[index - 1][0]
            separator = clock_gap if kind == "clock" or previous_kind == "clock" else status_gap
            out.append(separator)
            right_cursor += len(separator)

        start_col = right_cursor
        end_col = start_col + len(padded_part) - 1
        if kind in {"volume", "network", "bluetooth"}:
            hit_regions.append((start_col, end_col, kind, None))

        if kind == "volume":
            color = muted_fg if (volume_state and volume_state[1]) else str(volume_cfg.get("foreground", foreground))
        elif kind == "network":
            color = str(network_cfg.get("foreground", foreground))
            if wifi_state is not None and not wifi_state[0]:
                color = str(network_cfg.get("muted_foreground", muted_fg))
        elif kind == "bluetooth":
            color = str(bluetooth_cfg.get("foreground", foreground))
            if bluetooth_state is False:
                color = str(bluetooth_cfg.get("muted_foreground", muted_fg))
        else:
            color = foreground

        out += [fg(color), padded_part, RESET]
        right_cursor = end_col + 1

    out += [fg(foreground), right_padding, RESET]

    sys.stdout.write("".join(out))
    sys.stdout.flush()
    return hit_regions


def parse_mouse_events(buffer: str) -> tuple[list[tuple[int, int, int, bool]], str]:
    """Parse SGR mouse reports: ESC [ < button ; x ; y M/m."""
    events: list[tuple[int, int, int, bool]] = []
    pattern = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
    consumed_to = 0
    for match in pattern.finditer(buffer):
        events.append((int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4) == "M"))
        consumed_to = match.end()
    if consumed_to:
        buffer = buffer[consumed_to:]
    elif len(buffer) > 128:
        buffer = buffer[-128:]
    return events, buffer


def handle_mouse(cfg: dict, regions: list[tuple[int, int, str, int | None]],
                 button: int, x: int, y: int, pressed: bool) -> None:
    if y != 1:
        return
    # Wheel events are press-only. Normal clicks are handled on press too so
    # there is no visible lag waiting for the corresponding release packet.
    if not pressed:
        return

    region = next((r for r in regions if r[0] <= x <= r[1]), None)
    if region is None:
        return
    _start, _end, kind, payload = region

    base = button & 0x7f
    if kind == "workspace" and payload is not None and base == 0:
        dispatch_workspace(payload)
    elif kind == "media":
        if base == 0:
            media_action(cfg, "play-pause")
        elif base == 1:
            media_action(cfg, "previous")
        elif base == 2:
            media_action(cfg, "next")
    elif kind == "volume":
        if base == 0:
            volume_action(cfg, "open")
        elif base == 1:
            volume_action(cfg, "mute")
        elif base == 64:
            volume_action(cfg, "up")
        elif base == 65:
            volume_action(cfg, "down")
    elif kind == "network" and base == 0:
        open_network(cfg)
    elif kind == "bluetooth" and base == 0:
        open_bluetooth(cfg)


def event_socket_path() -> Path | None:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not runtime or not signature:
        return None
    return Path(runtime) / "hypr" / signature / ".socket2.sock"


def connect_event_socket() -> socket.socket | None:
    path = event_socket_path()
    if path is None:
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(str(path))
        sock.setblocking(False)
        return sock
    except OSError:
        sock.close()
        return None


def event_needs_workspace_refresh(line: str) -> bool:
    return line.startswith((
        "workspace>>",
        "workspacev2>>",
        "focusedmon>>",
        "focusedmonv2>>",
        "createworkspace>>",
        "createworkspacev2>>",
        "destroyworkspace>>",
        "destroyworkspacev2>>",
        "moveworkspace>>",
        "moveworkspacev2>>",
        "openwindow>>",
        "closewindow>>",
        "movewindow>>",
        "movewindowv2>>",
    ))


def stop(*_args) -> None:
    global RUNNING
    RUNNING = False


def main() -> int:
    if shutil.which("hyprctl") is None:
        print("kitty-desktop: hyprctl was not found", file=sys.stderr)
        return 1

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    cfg = load_config()
    interval = max(0.1, float(cfg.get("bar", {}).get("clock_interval_seconds", 1.0)))
    media_cfg = cfg.get("media", {})
    media_backend = str(media_cfg.get("backend", "mpd")).strip().lower()
    media_interval = max(0.2, float(media_cfg.get("interval_seconds", 0.5)))
    volume_cfg = cfg.get("volume", {})
    volume_fallback_interval = max(0.2, float(volume_cfg.get("fallback_interval_seconds", 0.5)))
    status_interval = max(0.5, float(cfg.get("status", {}).get("interval_seconds", 2.0)))

    existing, active = query_workspace_state()
    media_state = query_media(cfg)
    volume_state = query_volume(cfg)
    wifi_state = query_wifi(cfg)
    bluetooth_state = query_bluetooth(cfg)

    last_clock_tick = 0.0
    last_media_tick = time.monotonic()
    last_volume_tick = last_media_tick
    last_status_tick = last_media_tick

    sock = connect_event_socket()
    buffer = b""
    mouse_buffer = ""
    audio_events = AudioEventSource() if bool(volume_cfg.get("enabled", True)) else None
    mpd_events = (
        MPDIdleSource(cfg)
        if bool(media_cfg.get("enabled", True)) and media_backend == "mpd"
        else None
    )

    stdin_fd: int | None = None
    old_termios = None
    try:
        if sys.stdin.isatty():
            stdin_fd = sys.stdin.fileno()
            old_termios = termios.tcgetattr(stdin_fd)
            tty.setcbreak(stdin_fd)
            os.set_blocking(stdin_fd, False)
    except (OSError, termios.error):
        stdin_fd = None
        old_termios = None

    # 1000 = button/wheel reporting, 1006 = SGR coordinates. Pointer input is
    # independent of the layer-shell keyboard focus policy, so the bar remains
    # keyboard-noninteractive while still receiving clicks.
    sys.stdout.write("\x1b[?25l\x1b[?1000h\x1b[?1006h\x1b[2J\x1b[H")
    sys.stdout.flush()

    hit_regions: list[tuple[int, int, str, int | None]] = []

    try:
        # A fresh Hyprland session/output reconnect can expose a transient tiny
        # kitty surface. Never build the border from that geometry.
        chrome_drawn = draw_chrome(cfg, wait_for_stable=True)
        chrome_retry_at = time.monotonic() + 0.50
        chrome_retry_count = 0

        hit_regions = render(cfg, existing, active, media_state, volume_state, wifi_state, bluetooth_state)
        last_clock_tick = time.monotonic()

        while RUNNING:
            now = time.monotonic()

            # If startup geometry was not ready, or just to correct any final
            # compositor resize during the first second, retry chrome a few
            # times. After that the bar returns to its normal event loop.
            if chrome_retry_count < 3 and now >= chrome_retry_at:
                if draw_chrome(cfg):
                    chrome_drawn = True
                chrome_retry_count += 1
                chrome_retry_at = now + 0.35

            until_clock = max(0.0, interval - (now - last_clock_tick))
            until_media = (
                max(0.0, media_interval - (now - last_media_tick))
                if media_backend == "mpris"
                else 3600.0
            )
            until_status = max(0.0, status_interval - (now - last_status_tick))
            audio_fd = audio_events.fileno() if audio_events is not None else None
            until_volume_fallback = (
                max(0.0, volume_fallback_interval - (now - last_volume_tick))
                if audio_events is not None and audio_fd is None
                else 3600.0
            )
            wait_for = min(until_clock, until_media, until_status, until_volume_fallback)
            dirty = False
            media_dirty = False
            volume_dirty = False
            mouse_dirty = False

            read_fds: list[object] = []
            if sock is not None:
                read_fds.append(sock)
            if audio_fd is not None:
                read_fds.append(audio_fd)
            mpd_fd = mpd_events.fileno() if mpd_events is not None else None
            if mpd_fd is not None:
                read_fds.append(mpd_fd)
            if stdin_fd is not None:
                read_fds.append(stdin_fd)

            if read_fds:
                try:
                    readable, _, _ = select.select(read_fds, [], [], wait_for)
                except (OSError, ValueError):
                    readable = []
            else:
                time.sleep(min(wait_for if wait_for else 0.2, 0.2))
                readable = []

            if sock is not None and sock in readable:
                try:
                    data = sock.recv(65536)
                    if not data:
                        sock.close()
                        sock = None
                    else:
                        buffer += data
                except OSError:
                    sock.close()
                    sock = None

                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    line = raw.decode("utf-8", "replace")
                    if event_needs_workspace_refresh(line):
                        dirty = True

            if audio_events is not None and audio_fd is not None and audio_fd in readable:
                volume_dirty = audio_events.drain()

            if mpd_events is not None and mpd_fd is not None and mpd_fd in readable:
                media_dirty = mpd_events.drain()

            if stdin_fd is not None and stdin_fd in readable:
                try:
                    data = os.read(stdin_fd, 4096).decode("utf-8", "replace")
                    mouse_buffer += data
                    events, mouse_buffer = parse_mouse_events(mouse_buffer)
                    for button, x, y, pressed in events:
                        handle_mouse(cfg, hit_regions, button, x, y, pressed)
                    mouse_dirty = bool(events)
                except (BlockingIOError, OSError):
                    pass

            if sock is None:
                new_sock = connect_event_socket()
                if new_sock is not None:
                    sock = new_sock
                    buffer = b""
                existing, active = query_workspace_state()
            elif dirty:
                existing, active = query_workspace_state()

            if audio_events is not None:
                audio_events.restart_if_dead()
            if mpd_events is not None and mpd_events.restart_if_needed():
                media_dirty = True

            now = time.monotonic()
            clock_due = now - last_clock_tick >= interval
            media_due = (
                media_backend == "mpris"
                and now - last_media_tick >= media_interval
            )
            status_due = now - last_status_tick >= status_interval
            volume_fallback_due = (
                audio_events is not None
                and audio_events.fileno() is None
                and now - last_volume_tick >= volume_fallback_interval
            )

            if media_due or media_dirty:
                media_state = query_media(cfg)
                last_media_tick = now
            if volume_dirty or volume_fallback_due or mouse_dirty:
                volume_state = query_volume(cfg)
                last_volume_tick = now
            if status_due:
                wifi_state = query_wifi(cfg)
                bluetooth_state = query_bluetooth(cfg)
                last_status_tick = now

            if clock_due or media_due or media_dirty or status_due or volume_dirty or volume_fallback_due or dirty or mouse_dirty:
                hit_regions = render(cfg, existing, active, media_state, volume_state, wifi_state, bluetooth_state)
                if clock_due:
                    last_clock_tick = now

    finally:
        if sock is not None:
            sock.close()
        if audio_events is not None:
            audio_events.close()
        if mpd_events is not None:
            mpd_events.close()
        sys.stdout.write(RESET + "\x1b[?1000l\x1b[?1006l\x1b[?25h\n")
        sys.stdout.flush()
        if stdin_fd is not None and old_termios is not None:
            try:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)
            except (OSError, termios.error):
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
