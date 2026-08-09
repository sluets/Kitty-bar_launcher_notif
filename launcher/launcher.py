#!/usr/bin/env python3
from __future__ import annotations

import configparser
import difflib
import json
import math
import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import termios
import time
import tomllib
import tty
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "launcher.toml"
HIDDEN_PATH = ROOT / "hidden-apps.toml"
PARENT_THEME = ROOT.parent / "theme.toml"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "kittyproto"
HISTORY_PATH = STATE_DIR / "launcher-history.json"

ESC = "\x1b"
CSI = ESC + "["
RESET = CSI + "0m"
BOLD = CSI + "1m"
DIM = CSI + "2m"
HIDE_CURSOR = CSI + "?25l"
SHOW_CURSOR = CSI + "?25h"
CLEAR = CSI + "2J" + CSI + "H"
ALT_SCREEN_ON = CSI + "?1049h"
ALT_SCREEN_OFF = CSI + "?1049l"

FIELD_CODE = re.compile(r"%(?:[fFuUdDnNickvm])")
RGBA_RE = re.compile(r"rgba\(([0-9a-fA-F]{8})\)")
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def hex_rgb(value: str, fallback: str = "#d8dee9") -> tuple[int, int, int]:
    value = value.strip()
    m = HEX_RE.search(value)
    if not m:
        return hex_rgb(fallback, "#d8dee9") if value != fallback else (216, 222, 233)
    h = m.group()[1:]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def fg(value: str) -> str:
    r, g, b = hex_rgb(value)
    return f"{CSI}38;2;{r};{g};{b}m"


def bg(value: str) -> str:
    r, g, b = hex_rgb(value)
    return f"{CSI}48;2;{r};{g};{b}m"


def hyprland_accent(fallback: str) -> str:
    if not shutil.which("hyprctl"):
        return fallback
    try:
        p = subprocess.run(
            ["hyprctl", "getoption", "general:col.active_border"],
            capture_output=True, text=True, timeout=0.6, check=False,
        )
        m = RGBA_RE.search(p.stdout)
        if m:
            return "#" + m.group(1)[:6]
    except Exception:
        pass
    return fallback


@dataclass(slots=True)
class App:
    desktop_id: str
    path: Path
    name: str
    generic_name: str
    comment: str
    keywords: tuple[str, ...]
    exec_line: str
    icon: str
    terminal: bool
    working_dir: str

    @property
    def haystack(self) -> str:
        return " ".join((self.name, self.generic_name, self.comment, *self.keywords)).lower()


def desktop_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    raw = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    dirs = [home / "applications"]
    dirs.extend(Path(p) / "applications" for p in raw.split(":") if p)
    # Earlier directories override later ones per XDG convention.
    return dirs


def current_desktops() -> set[str]:
    value = os.environ.get("XDG_CURRENT_DESKTOP", "Hyprland")
    return {x.casefold() for x in re.split(r"[:;]", value) if x}


def bool_value(section, key: str, default: bool = False) -> bool:
    try:
        return section.getboolean(key, fallback=default)
    except ValueError:
        return default


def parse_list(value: str) -> set[str]:
    return {x.casefold() for x in value.split(";") if x.strip()}


def discover_apps() -> list[App]:
    desktops = current_desktops()
    seen: set[str] = set()
    apps: list[App] = []

    for base in desktop_dirs():
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.desktop")):
            try:
                desktop_id = str(path.relative_to(base)).replace("/", "-")
            except ValueError:
                desktop_id = path.name
            if desktop_id in seen:
                continue
            seen.add(desktop_id)

            cp = configparser.ConfigParser(interpolation=None, strict=False)
            cp.optionxform = str
            try:
                cp.read(path, encoding="utf-8")
                entry = cp["Desktop Entry"]
            except Exception:
                continue

            if entry.get("Type", "Application") != "Application":
                continue
            if bool_value(entry, "Hidden") or bool_value(entry, "NoDisplay"):
                continue

            only = parse_list(entry.get("OnlyShowIn", ""))
            deny = parse_list(entry.get("NotShowIn", ""))
            if only and desktops.isdisjoint(only):
                continue
            if deny and not desktops.isdisjoint(deny):
                continue

            tryexec = entry.get("TryExec", "").strip()
            if tryexec and not (Path(tryexec).is_file() or shutil.which(tryexec)):
                continue

            name = entry.get("Name", "").strip()
            exec_line = entry.get("Exec", "").strip()
            if not name or not exec_line:
                continue

            apps.append(App(
                desktop_id=desktop_id,
                path=path,
                name=name,
                generic_name=entry.get("GenericName", "").strip(),
                comment=entry.get("Comment", "").strip(),
                keywords=tuple(x.strip() for x in entry.get("Keywords", "").split(";") if x.strip()),
                exec_line=exec_line,
                icon=entry.get("Icon", "").strip(),
                terminal=bool_value(entry, "Terminal"),
                working_dir=entry.get("Path", "").strip(),
            ))
    return apps


def load_history() -> dict[str, dict]:
    try:
        data = json.loads(HISTORY_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_history(history: dict[str, dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n")
    tmp.replace(HISTORY_PATH)


def hidden_ids() -> set[str]:
    data = load_toml(HIDDEN_PATH)
    raw = data.get("hidden", [])
    return {str(x) for x in raw if isinstance(x, str)}


def save_hidden(ids: set[str]) -> None:
    # Desktop IDs cannot contain a literal quote in practice; still escape for TOML.
    lines = [
        "# Apps hidden with Alt+H are written here by the launcher.",
        "# Use the desktop-file ID, not the display name.",
        "hidden = [",
    ]
    for app_id in sorted(ids, key=str.casefold):
        escaped = app_id.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'    "{escaped}",')
    lines.append("]")
    HIDDEN_PATH.write_text("\n".join(lines) + "\n")


def aliases(config: dict) -> dict[str, tuple[str, ...]]:
    raw = config.get("aliases", {})
    out = {}
    if isinstance(raw, dict):
        for key, vals in raw.items():
            if isinstance(vals, list):
                out[str(key)] = tuple(str(x).lower() for x in vals)
    return out


def edit_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def fuzzy_text_score(query: str, app: App, app_aliases: tuple[str, ...]) -> float:
    q = query.casefold().strip()
    if not q:
        return 0.0
    name = app.name.casefold()
    words = re.findall(r"[\w.+-]+", name)
    searchable = [name, app.generic_name.casefold(), *[x.casefold() for x in app.keywords], *app_aliases]

    score = 0.0
    if name == q:
        score = max(score, 1000)
    if name.startswith(q):
        score = max(score, 820 - max(0, len(name) - len(q)) * 0.4)
    if any(w.startswith(q) for w in words):
        score = max(score, 700)
    if q in name:
        score = max(score, 610 - name.index(q) * 2)

    # Typo tolerance: compare against the full name and individual words.
    candidates = [name, *words, *searchable[1:]]
    for candidate in candidates:
        if not candidate:
            continue
        ratio = difflib.SequenceMatcher(None, q, candidate).ratio()
        # Short query typo handling benefits from direct edit distance.
        dist = edit_distance(q, candidate[: max(len(q), min(len(candidate), len(q) + 2))])
        typo_bonus = max(0.0, 1.0 - dist / max(len(q), 1))
        local = max(ratio, typo_bonus)
        if local >= 0.48:
            score = max(score, 180 + local * 360)

    haystack = app.haystack + " " + " ".join(app_aliases)
    # Ordered subsequence allows things like 'ffx' -> Firefox without making it
    # strong enough to outrank a clean prefix/exact match.
    it = iter(haystack)
    if all(ch in it for ch in q):
        score = max(score, 260 + min(120, len(q) * 10))
    return score


def usage_bonus(app_id: str, history: dict[str, dict], cfg: dict) -> float:
    item = history.get(app_id, {})
    count = max(0, int(item.get("launch_count", 0) or 0))
    last = float(item.get("last_launched", 0) or 0)
    search_cfg = cfg.get("search", {})
    fw = float(search_cfg.get("frequency_weight", 8.0))
    rw = float(search_cfg.get("recency_weight", 18.0))
    frequency = min(12.0, math.log2(count + 1)) * fw
    if last <= 0:
        recency = 0.0
    else:
        age_days = max(0.0, (time.time() - last) / 86400)
        recency = rw * math.exp(-age_days / 14.0)
    return frequency + recency


def rank_apps(apps: list[App], query: str, history: dict[str, dict], cfg: dict, alias_map: dict[str, tuple[str, ...]]) -> list[App]:
    usage_enabled = bool(cfg.get("search", {}).get("usage_ranking", True))
    scored = []
    if not query.strip():
        for app in apps:
            bonus = usage_bonus(app.desktop_id, history, cfg) if usage_enabled else 0.0
            scored.append((bonus, app.name.casefold(), app))
        # On a fresh install, alphabetical is less random than filesystem order.
        return [x[2] for x in sorted(scored, key=lambda x: (-x[0], x[1]))]

    for app in apps:
        text = fuzzy_text_score(query, app, alias_map.get(app.desktop_id, ()))
        if text <= 0:
            continue
        bonus = usage_bonus(app.desktop_id, history, cfg) if usage_enabled else 0.0
        # Keep usage a tiebreaker: a mediocre fuzzy hit must not beat a strong name hit.
        scored.append((text + min(110.0, bonus), text, app.name.casefold(), app))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [x[3] for x in scored]


def expand_exec(app: App) -> list[str]:
    # Handle the useful desktop-entry substitutions and remove file/url placeholders
    # since the launcher is starting the app without arguments.
    s = app.exec_line.replace("%%", "\0")
    s = s.replace("%c", shlex.quote(app.name))
    s = s.replace("%k", shlex.quote(str(app.path)))
    if "%i" in s:
        icon = f"--icon {shlex.quote(app.icon)}" if app.icon else ""
        s = s.replace("%i", icon)
    s = FIELD_CODE.sub("", s).replace("\0", "%")
    args = shlex.split(s)
    if app.terminal:
        args = ["kitty", "--"] + args
    return args


def launch(app: App, history: dict[str, dict]) -> bool:
    args = expand_exec(app)
    if not args:
        return False
    try:
        subprocess.Popen(
            args,
            cwd=app.working_dir or None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError):
        return False
    item = history.setdefault(app.desktop_id, {})
    item["launch_count"] = int(item.get("launch_count", 0) or 0) + 1
    item["last_launched"] = time.time()
    save_history(history)
    return True


def terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size((84, 20))
    return size.columns, size.lines


def crop(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


class Launcher:
    def __init__(self, show_hidden: bool = False):
        self.cfg = load_toml(CONFIG_PATH)
        parent = load_toml(PARENT_THEME)
        appearance = dict(self.cfg.get("appearance", {}))
        text = parent.get("text", {}) if isinstance(parent.get("text"), dict) else {}
        bar = parent.get("bar", {}) if isinstance(parent.get("bar"), dict) else {}
        border = parent.get("border", {}) if isinstance(parent.get("border"), dict) else {}
        appearance["background"] = str(bar.get("background", appearance.get("background", "#101318")))
        appearance["foreground"] = str(text.get("foreground", appearance.get("foreground", "#d8dee9")))
        appearance["muted"] = str(text.get("muted", appearance.get("muted", "#687080")))
        fallback = appearance.get("accent", "#33ccff")
        fb = border.get("fallback_colors", [])
        if isinstance(fb, list) and fb:
            fallback = str(fb[0])
        appearance["accent"] = hyprland_accent(str(fallback))
        self.ap = appearance

        self.all_apps = discover_apps()
        self.hidden = hidden_ids()
        self.show_hidden = show_hidden
        self.history = load_history()
        self.alias_map = aliases(self.cfg)
        self.query = ""
        self.selected = 0
        self.offset = 0
        self.message = ""
        self.old_term = None
        self.running = True

    def visible_apps(self) -> list[App]:
        if self.show_hidden:
            return self.all_apps
        return [a for a in self.all_apps if a.desktop_id not in self.hidden]

    def ranked(self) -> list[App]:
        return rank_apps(self.visible_apps(), self.query, self.history, self.cfg, self.alias_map)

    def render(self):
        cols, rows = terminal_size()
        max_results_cfg = max(1, int(self.cfg.get("launcher", {}).get("max_results", 10)))
        # Prompt + count + separator consume three rows. Hyprland owns the
        # outer border now, and there is no footer.
        max_results = max(1, min(max_results_cfg, rows - 3))
        results = self.ranked()
        if results:
            self.selected = min(self.selected, len(results) - 1)
        else:
            self.selected = 0
        if self.selected < self.offset:
            self.offset = self.selected
        if self.selected >= self.offset + max_results:
            self.offset = self.selected - max_results + 1

        border_c = fg(self.ap["accent"])
        text_c = fg(self.ap["foreground"])
        muted_c = fg(self.ap["muted"])
        sel_fg = fg(self.ap.get("selection_foreground", "#101318"))
        sel_bg = bg(self.ap["accent"])

        # Rev 5.5 is a normal kitty OS window. Hyprland supplies the actual
        # window border/rounding, so do not draw a second terminal border here.
        inner = max(20, cols)
        lines: list[str] = []

        prompt = "Search  " + self.query + "▏"
        lines.append(text_c + "  " + crop(prompt, inner - 4).ljust(inner - 4) + "  " + RESET)

        if self.cfg.get("launcher", {}).get("show_count", True):
            count = f"{len(results)}/{len(self.visible_apps())}"
            if self.show_hidden:
                count += "  hidden visible"
            lines.append(muted_c + "  " + crop(count, inner - 4).ljust(inner - 4) + "  " + RESET)

        # Keep a simple internal separator. The outer chrome is all Hyprland.
        lines.append(border_c + "─" * inner + RESET)

        page = results[self.offset:self.offset + max_results]
        for i in range(max_results):
            if i < len(page):
                app = page[i]
                absolute = self.offset + i
                prefix = f"{absolute + 1:>2}. " if self.cfg.get("launcher", {}).get("number_results", True) else ""
                hidden_mark = "◌ " if app.desktop_id in self.hidden else ""
                label = prefix + hidden_mark + app.name
                if self.cfg.get("launcher", {}).get("show_descriptions", False) and app.comment:
                    label += "  —  " + app.comment
                label = "  " + crop(label, inner - 4).ljust(inner - 4) + "  "
                if absolute == self.selected:
                    lines.append(sel_bg + sel_fg + BOLD + label + RESET)
                else:
                    lines.append(text_c + label + RESET)
            else:
                lines.append(" " * inner)

        # Home each physical row explicitly. Do not depend on terminal newline
        # translation; this remains correct even if input mode changes later.
        out = [CLEAR, HIDE_CURSOR]
        for row, line in enumerate(lines, 1):
            out.append(f"{CSI}{row};1H{line}")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        return results

    def read_key(self) -> str:
        ch = os.read(sys.stdin.fileno(), 1)
        if not ch:
            return "EOF"
        if ch == b"\x1b":
            # Consume any immediately available escape-sequence bytes.
            seq = bytearray(ch)
            end = time.monotonic() + 0.025
            while time.monotonic() < end:
                ready, _, _ = select.select([sys.stdin], [], [], max(0, end - time.monotonic()))
                if not ready:
                    break
                seq.extend(os.read(sys.stdin.fileno(), 1))
                if len(seq) >= 8:
                    break
            s = bytes(seq)
            mapping = {b"\x1b[A": "UP", b"\x1b[B": "DOWN", b"\x1b[H": "HOME", b"\x1b[F": "END", b"\x1bh": "ALT_H", b"\x1bH": "ALT_H"}
            return mapping.get(s, "ESC")
        if ch in (b"\r", b"\n"):
            return "ENTER"
        if ch in (b"\x7f", b"\x08"):
            return "BACKSPACE"
        if ch == b"\x15":
            return "CTRL_U"
        if ch == b"\x03":
            return "ESC"
        try:
            return ch.decode("utf-8")
        except UnicodeDecodeError:
            # Read remainder of a UTF-8 sequence if the user types non-ASCII.
            data = bytearray(ch)
            for _ in range(3):
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    data.extend(os.read(sys.stdin.fileno(), 1))
            return ""

    def toggle_hidden(self, results: list[App]):
        if not results:
            return
        app = results[self.selected]
        if app.desktop_id in self.hidden:
            self.hidden.remove(app.desktop_id)
            self.message = f"Unhidden: {app.name}"
        else:
            self.hidden.add(app.desktop_id)
            self.message = f"Hidden: {app.name}"
        save_hidden(self.hidden)
        self.selected = 0
        self.offset = 0

    def run(self):
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("kittyproto launcher must run inside a terminal", file=sys.stderr)
            return 2
        self.old_term = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
        sys.stdout.write(ALT_SCREEN_ON + CLEAR + HIDE_CURSOR)
        sys.stdout.flush()
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        try:
            while self.running:
                results = self.render()
                key = self.read_key()
                self.message = ""
                if key in ("ESC", "EOF"):
                    break
                if key == "UP":
                    self.selected = max(0, self.selected - 1)
                elif key == "DOWN":
                    self.selected = min(max(0, len(results) - 1), self.selected + 1)
                elif key == "HOME":
                    self.selected = self.offset = 0
                elif key == "END" and results:
                    self.selected = len(results) - 1
                elif key == "ENTER" and results:
                    if launch(results[self.selected], self.history):
                        break
                    self.message = f"Could not launch: {results[self.selected].name}"
                elif key == "ALT_H":
                    self.toggle_hidden(results)
                elif key == "CTRL_U":
                    self.query = ""
                    self.selected = self.offset = 0
                elif key == "BACKSPACE":
                    if self.query:
                        self.query = self.query[:-1]
                        self.selected = self.offset = 0
                elif len(key) == 1 and key.isprintable():
                    self.query += key
                    self.selected = self.offset = 0
        finally:
            if self.old_term:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.old_term)
            sys.stdout.write(RESET + SHOW_CURSOR + CLEAR + ALT_SCREEN_OFF)
            sys.stdout.flush()
        return 0


def dump_apps(show_hidden: bool):
    cfg = load_toml(CONFIG_PATH)
    history = load_history()
    hs = hidden_ids()
    apps = discover_apps()
    if not show_hidden:
        apps = [a for a in apps if a.desktop_id not in hs]
    for app in rank_apps(apps, "", history, cfg, aliases(cfg)):
        print(f"{app.desktop_id}\t{app.name}\t{app.exec_line}")


def main() -> int:
    args = set(sys.argv[1:])
    if "--dump" in args:
        dump_apps("--show-hidden" in args)
        return 0
    return Launcher(show_hidden="--show-hidden" in args).run()


if __name__ == "__main__":
    raise SystemExit(main())
