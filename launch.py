#!/usr/bin/env python3
"""Launch kitty-desktop Rev 3 with explicit panel/exclusive-zone geometry."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError as exc:
    raise SystemExit("kitty-desktop requires Python 3.11+ (tomllib)") from exc

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "theme.toml"
PANEL_CONFIG = ROOT / "panel.conf"


def load_config() -> dict:
    with CONFIG.open("rb") as fh:
        return tomllib.load(fh)


def hypr_getoption(name: str) -> str:
    if shutil.which("hyprctl") is None:
        return ""
    try:
        proc = subprocess.run(
            ["hyprctl", "getoption", name],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        return proc.stdout + "\n" + proc.stderr
    except OSError:
        return ""


def parse_first_number(text: str, fallback: float) -> float:
    # Prefer the common "int:" / "float:" style output, then fall back to the
    # first standalone numeric value. This is deliberately tolerant of format
    # changes between Hyprland revisions.
    match = re.search(r"(?:int|float):\s*(-?\d+(?:\.\d+)?)", text, re.I)
    if not match:
        match = re.search(r"(?<![0-9a-fA-F])(-?\d+(?:\.\d+)?)(?![0-9a-fA-F])", text)
    return float(match.group(1)) if match else fallback


def normalize_hex(token: str, *, alpha_first: bool = False) -> str | None:
    token = token.strip().lower().lstrip("#")
    if token.startswith("0x"):
        token = token[2:]
    if len(token) == 8 and all(c in "0123456789abcdef" for c in token):
        token = token[2:] if alpha_first else token[:6]
    if len(token) == 6 and all(c in "0123456789abcdef" for c in token):
        return "#" + token
    return None


def parse_gradient(text: str, fallback_colors: list[str], fallback_angle: float) -> tuple[list[str], float]:
    colors: list[str] = []

    # Preserve explicit rgba()/rgb() order. Hyprland's rgba is RRGGBBAA.
    for match in re.finditer(r"rgba?\((?:0x)?([0-9a-fA-F]{6,8})\)", text):
        color = normalize_hex(match.group(1), alpha_first=False)
        if color and color not in colors:
            colors.append(color)

    # Some getoption output exposes packed 0xAARRGGBB values instead.
    if not colors:
        for match in re.finditer(r"0x([0-9a-fA-F]{8})", text):
            color = normalize_hex(match.group(1), alpha_first=True)
            if color and color not in colors:
                colors.append(color)

    if not colors:
        colors = list(fallback_colors)

    angle_match = re.search(r"(-?\d+(?:\.\d+)?)\s*deg", text, re.I)
    angle = float(angle_match.group(1)) if angle_match else fallback_angle
    return colors, angle


def main() -> int:
    if shutil.which("kitten") is None:
        print("kitty-desktop: 'kitten' was not found in PATH", file=sys.stderr)
        return 1

    cfg = load_config()
    sync = cfg.get("hyprland", {})
    bar = cfg.get("bar", {})
    text = cfg.get("text", {})
    border = cfg.get("border", {})

    height_px = max(16, int(bar.get("height_px", 34)))
    # Keep visible panel height and compositor reservation separate. By default
    # reserve exactly the visible chrome height; bottom_gap_px is the only extra
    # space we intentionally ask Hyprland to keep below the bar.
    reserve_height = bar.get("reserve_height_px", "auto")
    bottom_gap = max(0, int(bar.get("bottom_gap_px", 0)))
    if isinstance(reserve_height, str) and reserve_height.strip().lower() == "auto":
        exclusive_zone = height_px + bottom_gap
    else:
        try:
            exclusive_zone = max(0, int(reserve_height)) + bottom_gap
        except (TypeError, ValueError):
            exclusive_zone = height_px + bottom_gap
    floating = bool(bar.get("floating", True))
    gaps_in = max(0.0, float(bar.get("gaps_in_pt", 4)))
    font_size = max(4.0, float(text.get("size", 11.0)))
    foreground = str(text.get("foreground", "#d8dee9"))

    fallback_colors = [str(x) for x in border.get("fallback_colors", ["#33ccff", "#ff66cc"])]
    if not fallback_colors:
        fallback_colors = ["#33ccff"]
    fallback_angle = float(border.get("angle_deg", 45.0))

    colors, angle = fallback_colors, fallback_angle
    if bool(sync.get("sync_border_colors", True)) or bool(sync.get("sync_border_angle", True)):
        parsed_colors, parsed_angle = parse_gradient(
            hypr_getoption("general:col.active_border"), fallback_colors, fallback_angle
        )
        if bool(sync.get("sync_border_colors", True)):
            colors = parsed_colors
        if bool(sync.get("sync_border_angle", True)):
            angle = parsed_angle

    # Keep the theme value as the trusted fallback. During the first moments of
    # a Hyprland session, `hyprctl getoption` can transiently return incomplete
    # or otherwise unusable state. A bogus large border width makes the entire
    # chrome image become "border" (solid gradient), because no inner fill is
    # left. Only accept compositor values in a deliberately sane range.
    theme_border_width = max(0, int(border.get("width_px", 2)))
    border_width = theme_border_width

    if bool(sync.get("sync_border_width", True)):
        raw_border = hypr_getoption("general:border_size")
        parsed_border = round(parse_first_number(raw_border, float(theme_border_width)))

        # Hyprland window borders larger than this are not useful for this
        # 34px-high panel. Reject transient/garbage startup values instead of
        # allowing them to consume the entire chrome.
        max_sane_border = max(4, min(8, height_px // 4))
        if 0 <= parsed_border <= max_sane_border:
            border_width = parsed_border

    if not bool(border.get("enabled", True)):
        border_width = 0

    # Treat the theme radius as the trusted fallback for the same reason as
    # border width above: Hyprland can briefly report absurd startup values
    # (observed: decoration:rounding -> 1000). A radius larger than half the
    # panel height has no useful visual meaning for this bar.
    theme_radius = max(0, int(bar.get("radius_px", 12)))
    radius = theme_radius

    if bool(sync.get("sync_rounding", True)):
        raw_radius = hypr_getoption("decoration:rounding")
        parsed_radius = round(parse_first_number(raw_radius, float(theme_radius)))
        max_sane_radius = max(0, height_px // 2)

        if 0 <= parsed_radius <= max_sane_radius:
            radius = parsed_radius

    gaps_out = max(0, int(bar.get("gaps_out_px", 8))) if floating else 0
    if floating and bool(sync.get("sync_gaps_out", True)):
        gaps_out = max(0, round(parse_first_number(
            hypr_getoption("general:gaps_out"), float(gaps_out)
        )))

    background = str(bar.get("background", "#101318"))
    opacity = min(1.0, max(0.0, float(bar.get("background_opacity", 0.88))))

    args = [
        "kitten", "panel",
        "--edge=top",
        "--layer=top",
        f"--lines={height_px}px",
        # Do not let panel defaults implicitly choose the reservation. Making
        # this explicit lets us diagnose/tune bottom spacing independently of
        # the visible panel and its top/side margins.
        "--override-exclusive-zone",
        f"--exclusive-zone={exclusive_zone}",
        "--app-id=kitty-desktop-bar",
        "--focus-policy=not-allowed",
        f"--config={PANEL_CONFIG}",
        f"--margin-top={gaps_out}",
        f"--margin-left={gaps_out}",
        f"--margin-right={gaps_out}",
        "-o", f"font_size={font_size}",
        # Transparent terminal canvas. The chrome PNG supplies the visible bg.
        "-o", "background_opacity=0",
        "-o", f"background={background}",
        "-o", f"foreground={foreground}",
        "-o", f"window_padding_width={gaps_in}",
        "-o", "window_border_width=0",
    ]

    output = sys.argv[1] if len(sys.argv) > 1 else ""
    if output:
        args.append(f"--output-name={output}")

    env = os.environ.copy()
    env["KITTY_DESKTOP_THEME"] = str(CONFIG)
    env["KITTY_DESKTOP_BORDER_COLORS"] = ",".join(colors)
    env["KITTY_DESKTOP_BORDER_COLOR"] = colors[0]
    env["KITTY_DESKTOP_BORDER_ANGLE"] = str(angle)
    env["KITTY_DESKTOP_BORDER_WIDTH"] = str(border_width)
    env["KITTY_DESKTOP_RADIUS"] = str(radius)
    env["KITTY_DESKTOP_BACKGROUND"] = background
    env["KITTY_DESKTOP_BACKGROUND_OPACITY"] = str(opacity)

    # This goes to the per-output supervisor log because launch.py inherits the
    # supervisor's stdout/stderr. It gives us the exact startup values if the
    # chrome ever renders incorrectly again.
    print(
        "kittyproto resolved chrome:"
        f" output={output or '<default>'}"
        f" height={height_px}"
        f" border={border_width}"
        f" radius={radius}"
        f" colors={','.join(colors)}"
        f" angle={angle}"
        f" opacity={opacity}",
        file=sys.stderr,
        flush=True,
    )

    args.append(str(ROOT / "bar.py"))
    os.execvpe(args[0], args, env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
