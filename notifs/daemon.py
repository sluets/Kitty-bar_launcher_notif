#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import signal as pysignal
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
import struct
import zlib
from typing import Any

try:
    from dbus_next import Variant
    from dbus_next.aio import MessageBus
    from dbus_next.constants import NameFlag, RequestNameReply
    from dbus_next.errors import DBusError
    from dbus_next.service import ServiceInterface, method, signal
except ImportError:
    print("kittyproto notifs requires python-dbus-next.", file=sys.stderr)
    print("Install it on Arch with: sudo pacman -S python-dbus-next", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "notifs.toml"
KITTY_CONFIG = ROOT / "kitty-notif.conf"
TOAST_SCRIPT = ROOT / "toast.py"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "kittyproto"
HISTORY_PATH = STATE_DIR / "notification-history.json"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/kittyproto-{os.getuid()}")) / "kittyproto-notifs"
PID_PATH = RUNTIME_DIR / "daemon.pid"
LOG_PREFIX = "[kittyproto-notifs]"


def load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return {}


def variant_value(value: Any, default: Any = None) -> Any:
    if isinstance(value, Variant):
        return value.value
    return value if value is not None else default




def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png_from_raw(
    width: int,
    height: int,
    rowstride: int,
    has_alpha: bool,
    bits_per_sample: int,
    channels: int,
    data: bytes,
    out_path: Path,
) -> bool:
    try:
        width = int(width)
        height = int(height)
        rowstride = int(rowstride)
        channels = int(channels)
        bits_per_sample = int(bits_per_sample)
    except Exception:
        return False
    if width <= 0 or height <= 0 or bits_per_sample != 8 or channels not in (3, 4):
        return False
    expected = rowstride * height
    if len(data) < expected or rowstride < width * channels:
        return False

    color_type = 6 if has_alpha or channels == 4 else 2
    rows = []
    for y in range(height):
        start = y * rowstride
        row = data[start : start + (width * channels)]
        rows.append(b'\x00' + row)
    payload = zlib.compress(b''.join(rows), level=9)
    png = b'\x89PNG\r\n\x1a\n'
    png += _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, color_type, 0, 0, 0))
    png += _chunk(b'IDAT', payload)
    png += _chunk(b'IEND', b'')
    out_path.write_bytes(png)
    return True


def resolve_image_path(app_icon: str, hints: dict[str, Variant], out_path: Path) -> str:
    # Prefer the raw Freedesktop image payload. This is what many notification
    # daemons use for album art and guarantees we hand kitty a PNG.
    for key in ('image-data', 'image_data', 'icon_data'):
        raw = variant_value(hints.get(key))
        if not raw:
            continue
        try:
            width, height, rowstride, has_alpha, bits_per_sample, channels, pixels = raw
            if isinstance(pixels, list):
                pixels = bytes(int(x) & 0xFF for x in pixels)
            elif not isinstance(pixels, (bytes, bytearray)):
                pixels = bytes(pixels)
            if write_png_from_raw(width, height, rowstride, has_alpha, bits_per_sample, channels, pixels, out_path):
                return str(out_path)
        except Exception:
            continue

    def _clean_candidate(v: Any) -> str:
        v = variant_value(v, '')
        if isinstance(v, bytes):
            return ''
        s = str(v or '').strip()
        if s.startswith('file://'):
            s = s[7:]
        return s

    for key in ('image-path', 'image_path'):
        s = _clean_candidate(hints.get(key))
        if s and Path(s).is_file():
            return s

    s = _clean_candidate(app_icon)
    if s and ('/' in s or s.startswith('.')) and Path(s).expanduser().is_file():
        return str(Path(s).expanduser())

    return ''

def safe_text(value: str, max_chars: int) -> str:
    value = str(value or "")
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 1)] + "…"


@dataclass(slots=True)
class Notice:
    id: int
    app_name: str
    app_icon: str
    summary: str
    body: str
    actions: list[str]
    hints: dict[str, Variant]
    urgency: int
    timeout_ms: int
    created_at: float
    generation: int = 0
    timer_task: asyncio.Task | None = None


@dataclass(slots=True)
class Slot:
    index: int
    data_path: Path
    process: subprocess.Popen | None = None
    watch_task: asyncio.Task | None = None
    notice_id: int | None = None
    stopping: bool = False


class Notifications(ServiceInterface):
    def __init__(self, manager: "NotificationManager"):
        super().__init__("org.freedesktop.Notifications")
        self.manager = manager

    @method()
    def GetCapabilities(self) -> "as":
        return ["body"]

    @method()
    async def Notify(
        self,
        app_name: "s",
        replaces_id: "u",
        app_icon: "s",
        summary: "s",
        body: "s",
        actions: "as",
        hints: "a{sv}",
        expire_timeout: "i",
    ) -> "u":
        return await self.manager.notify(
            app_name, replaces_id, app_icon, summary, body,
            list(actions), dict(hints), expire_timeout,
        )

    @method()
    async def CloseNotification(self, id: "u"):
        if not await self.manager.close(int(id), 3):
            raise DBusError(
                "org.freedesktop.Notifications.InvalidNotification",
                f"notification {id} does not exist",
            )

    @method()
    def GetServerInformation(self) -> "ssss":
        return ["kittyproto", "kittyproto", "0.8.0", "1.3"]

    @signal()
    def NotificationClosed(self, id: "u", reason: "u") -> "uu":
        return [id, reason]

    @signal()
    def ActionInvoked(self, id: "u", action_key: "s") -> "us":
        return [id, action_key]


class NotificationManager:
    def __init__(self, config: dict):
        self.config = config
        daemon = config.get("daemon", {})
        toast = config.get("toast", {})
        self.max_visible = max(1, min(8, int(daemon.get("max_visible", 4))))
        self.history_limit = max(1, int(daemon.get("history_limit", 100)))
        self.default_timeouts = {
            0: int(daemon.get("low_timeout_ms", 3000)),
            1: int(daemon.get("normal_timeout_ms", 5000)),
            2: int(daemon.get("critical_timeout_ms", 0)),
        }
        self.toast_cfg = toast
        self.colors = config.get("colors", {})
        replacement = config.get("replacement", {})
        self.force_new_apps = [str(x).strip().lower() for x in replacement.get("force_new_apps", []) if str(x).strip()]
        self.notices: dict[int, Notice] = {}
        self.visible_order: list[int] = []
        self.next_id = 1
        self.interface: Notifications | None = None
        self.history = self._load_history()
        self._lock = asyncio.Lock()
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        self.slots = [Slot(i, RUNTIME_DIR / f"slot-{i}.json") for i in range(self.max_visible)]

    def _load_history(self) -> list[dict]:
        try:
            raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            return raw if isinstance(raw, list) else []
        except Exception:
            return []

    def _save_history(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.history[-self.history_limit:], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(HISTORY_PATH)

    def _history_add(self, n: Notice) -> None:
        self.history.append({
            "id": n.id,
            "timestamp": n.created_at,
            "app_name": n.app_name,
            "summary": n.summary,
            "body": n.body,
            "urgency": n.urgency,
            "closed_reason": None,
        })
        self.history = self.history[-self.history_limit:]
        self._save_history()

    def _history_close(self, id: int, reason: int) -> None:
        for item in reversed(self.history):
            if item.get("id") == id and item.get("closed_reason") is None:
                item["closed_reason"] = reason
                item["closed_at"] = time.time()
                break
        self._save_history()

    def _alloc_id(self) -> int:
        while self.next_id == 0 or self.next_id in self.notices:
            self.next_id = (self.next_id + 1) & 0xFFFFFFFF
            if self.next_id == 0:
                self.next_id = 1
        value = self.next_id
        self.next_id = (self.next_id + 1) & 0xFFFFFFFF
        if self.next_id == 0:
            self.next_id = 1
        return value

    def _urgency(self, hints: dict[str, Variant]) -> int:
        raw = variant_value(hints.get("urgency"), 1)
        try:
            return min(2, max(0, int(raw)))
        except Exception:
            return 1

    def _force_new(self, app_name: str) -> bool:
        name = str(app_name or "").strip().lower()
        return any(token in name for token in self.force_new_apps)

    def _timeout(self, requested: int, urgency: int) -> int:
        if urgency >= 2:
            return 0
        if requested == 0:
            return 0
        if requested < 0:
            return max(0, self.default_timeouts.get(urgency, 5000))
        return max(0, requested)

    async def notify(
        self,
        app_name: str,
        replaces_id: int,
        app_icon: str,
        summary: str,
        body: str,
        actions: list[str],
        hints: dict[str, Variant],
        expire_timeout: int,
    ) -> int:
        async with self._lock:
            urgency = self._urgency(hints)
            timeout_ms = self._timeout(expire_timeout, urgency)
            force_new = self._force_new(app_name)
            if replaces_id and replaces_id in self.notices and not force_new:
                id = int(replaces_id)
                old = self.notices[id]
                if old.timer_task:
                    old.timer_task.cancel()
                notice = Notice(
                    id=id,
                    app_name=app_name,
                    app_icon=app_icon,
                    summary=summary,
                    body=body,
                    actions=actions,
                    hints=hints,
                    urgency=urgency,
                    timeout_ms=timeout_ms,
                    created_at=time.time(),
                    generation=old.generation + 1,
                )
                self.notices[id] = notice
                if id in self.visible_order:
                    self.visible_order.remove(id)
                self.visible_order.insert(0, id)
            else:
                id = self._alloc_id()
                notice = Notice(
                    id=id,
                    app_name=app_name,
                    app_icon=app_icon,
                    summary=summary,
                    body=body,
                    actions=actions,
                    hints=hints,
                    urgency=urgency,
                    timeout_ms=timeout_ms,
                    created_at=time.time(),
                )
                self.notices[id] = notice
                self.visible_order.insert(0, id)

            self._history_add(notice)
            while len(self.visible_order) > self.max_visible:
                oldest = self.visible_order[-1]
                await self._close_locked(oldest, 2)

            self._sync_slots_locked()
            if timeout_ms > 0:
                notice.timer_task = asyncio.create_task(self._expire_after(notice.id, notice.generation, timeout_ms))
            return id

    def _payload(self, n: Notice) -> dict:
        image_path = resolve_image_path(n.app_icon, n.hints, RUNTIME_DIR / f"art-{n.id}-{n.generation}.png")
        return {
            "id": n.id,
            "app_name": safe_text(n.app_name, 80),
            "summary": safe_text(n.summary, int(self.toast_cfg.get("summary_max_chars", 60))),
            "body": safe_text(n.body, int(self.toast_cfg.get("body_max_chars", 180))),
            "urgency": n.urgency,
            "body_max_lines": int(self.toast_cfg.get("body_max_lines", 3)),
            "image_path": image_path,
            "art_width_cols": int(self.toast_cfg.get("art_width_cols", 11)),
            "art_height_rows": int(self.toast_cfg.get("art_height_rows", 5)),
            "art_x_col": int(self.toast_cfg.get("art_x_col", 3)),
            "art_y_row": int(self.toast_cfg.get("art_y_row", 2)),
            "art_gap_cols": int(self.toast_cfg.get("art_gap_cols", 2)),
            "text_x_offset_cols": int(self.toast_cfg.get("text_x_offset_cols", 0)),
            "text_y_row": int(self.toast_cfg.get("text_y_row", 2)),
            "colors": self.colors,
        }

    def _write_slot(self, slot: Slot, n: Notice) -> None:
        tmp = slot.data_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._payload(n), ensure_ascii=False), encoding="utf-8")
        tmp.replace(slot.data_path)
        slot.notice_id = n.id

    def _spawn_slot(self, slot: Slot) -> None:
        cmd = [
            "kitty",
            "--class", f"kittyproto-notif-{slot.index}",
            "--title", f"kittyproto-notif-{slot.index}",
            "--config", str(KITTY_CONFIG),
            "--override", f"font_size={float(self.toast_cfg.get('font_size', 12.0))}",
            "--override", f"background_opacity={float(self.toast_cfg.get('background_opacity', 0.94))}",
            "--override", f"initial_window_width={int(self.toast_cfg.get('width_cells', 46))}c",
            "--override", f"initial_window_height={int(self.toast_cfg.get('height_cells', 7))}c",
            str(TOAST_SCRIPT), str(slot.data_path),
        ]
        slot.stopping = False
        slot.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        slot.watch_task = asyncio.create_task(self._watch_slot(slot.index, slot.process))

    def _sync_slots_locked(self) -> None:
        desired = list(self.visible_order[: self.max_visible])
        for idx, slot in enumerate(self.slots):
            nid = desired[idx] if idx < len(desired) else None
            if nid is None:
                slot.notice_id = None
                if slot.process and slot.process.poll() is None:
                    slot.stopping = True
                    try:
                        slot.process.terminate()
                    except ProcessLookupError:
                        pass
                slot.process = None
                slot.data_path.unlink(missing_ok=True)
                continue

            n = self.notices.get(nid)
            if not n:
                continue
            self._write_slot(slot, n)
            if not slot.process or slot.process.poll() is not None:
                self._spawn_slot(slot)

    async def _watch_slot(self, index: int, proc: subprocess.Popen) -> None:
        await asyncio.to_thread(proc.wait)
        async with self._lock:
            slot = self.slots[index]
            if slot.process is not proc:
                return
            intentional = slot.stopping
            clicked_id = slot.notice_id
            slot.process = None
            slot.watch_task = None
            slot.stopping = False
            if intentional:
                return
            if clicked_id is not None and clicked_id in self.notices:
                await self._close_locked(clicked_id, 2)
                self._sync_slots_locked()

    async def _expire_after(self, id: int, generation: int, timeout_ms: int) -> None:
        try:
            await asyncio.sleep(timeout_ms / 1000)
            async with self._lock:
                n = self.notices.get(id)
                if n and n.generation == generation:
                    await self._close_locked(id, 1)
                    self._sync_slots_locked()
        except asyncio.CancelledError:
            pass

    async def close(self, id: int, reason: int) -> bool:
        async with self._lock:
            if id not in self.notices:
                return False
            await self._close_locked(id, reason)
            self._sync_slots_locked()
            return True

    async def _close_locked(self, id: int, reason: int) -> None:
        n = self.notices.pop(id, None)
        if not n:
            return
        if id in self.visible_order:
            self.visible_order.remove(id)
        if n.timer_task and n.timer_task is not asyncio.current_task():
            n.timer_task.cancel()
        self._history_close(id, reason)
        if self.interface:
            self.interface.NotificationClosed(id, reason)

    async def shutdown(self) -> None:
        async with self._lock:
            for n in list(self.notices.values()):
                if n.timer_task:
                    n.timer_task.cancel()
            self.notices.clear()
            self.visible_order.clear()
            for slot in self.slots:
                if slot.process and slot.process.poll() is None:
                    slot.stopping = True
                    try:
                        slot.process.terminate()
                    except ProcessLookupError:
                        pass
                slot.data_path.unlink(missing_ok=True)


async def amain() -> int:
    if not shutil.which("kitty"):
        print(f"{LOG_PREFIX} kitty not found", file=sys.stderr)
        return 1

    config = load_toml(CONFIG_PATH)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()) + "\n")

    manager = NotificationManager(config)
    interface = Notifications(manager)
    manager.interface = interface
    bus = await MessageBus().connect()
    bus.export("/org/freedesktop/Notifications", interface)
    reply = await bus.request_name("org.freedesktop.Notifications", NameFlag.DO_NOT_QUEUE)
    if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
        print(
            f"{LOG_PREFIX} another notification daemon already owns org.freedesktop.Notifications.\n"
            "Stop/mask Dunst first, then start kittyproto notifs again.",
            file=sys.stderr,
        )
        PID_PATH.unlink(missing_ok=True)
        return 2

    print(f"{LOG_PREFIX} ready: org.freedesktop.Notifications", flush=True)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (pysignal.SIGINT, pysignal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await stop_event.wait()
    await manager.shutdown()
    try:
        bus.disconnect()
    except Exception:
        pass
    PID_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(amain()))
    finally:
        try:
            PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass
