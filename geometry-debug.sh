#!/usr/bin/env bash
set -u
printf '%s\n' '--- kitty-desktop geometry ---'
grep -E '^(height_px|reserve_height_px|bottom_gap_px|gaps_out_px|gaps_in_pt|floating)\s*=' "$(dirname "$0")/theme.toml" || true
printf '\n%s\n' '--- Hyprland gaps_out ---'
hyprctl getoption general:gaps_out 2>/dev/null || true
printf '\n%s\n' '--- Hyprland monitors/reserved areas ---'
hyprctl -j monitors 2>/dev/null | python -m json.tool 2>/dev/null || hyprctl monitors 2>/dev/null || true
