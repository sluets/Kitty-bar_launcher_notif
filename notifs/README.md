# kittyproto notifications v0.7

v0.4 removes runtime `movewindowpixel` stacking entirely.

There are four fixed kitty/Hyprland notification slots:

- `kittyproto-notif-0` -> `{ 2110, 70 }`
- `kittyproto-notif-1` -> `{ 2110, 190 }`
- `kittyproto-notif-2` -> `{ 2110, 310 }`
- `kittyproto-notif-3` -> `{ 2110, 430 }`

Hyprland owns the position, size, border, rounding, shadow and animation of each slot. The daemon only changes which notification is displayed in each slot. When a notification closes, content is shifted upward between existing windows, so stack reflow does not depend on compositor IPC.

## Hyprland rule

Remove/replace the old single `^kittyproto-notif$` rule and copy `hyprland-rule.lua` into your loaded Hyprland Lua config. Reload:

```bash
hyprctl reload
```

## Restart daemon

```bash
~/.config/kittyproto/notifs/stop-notifs.sh
~/.config/kittyproto/notifs/start-notifs.sh
```

## Test one

```bash
notify-send -u critical "kittyproto" "single toast"
```

## Test four from fish

```fish
for i in 1 2 3 4
    notify-send -u critical "Toast $i" "stack test"
end
```

The previous `for ...; do ...; done` example was Bash syntax and will not run in fish.

## Behavior

- newest notification occupies slot 0
- older notifications shift down to slots 1-3
- closing/expiring a notification updates the remaining slot contents upward
- max visible: 4
- critical notifications do not auto-expire
- normal/low notifications use configured timeouts
- click a toast to dismiss it
- history remains in `~/.local/state/kittyproto/notification-history.json`


## v0.5 replacement policy

Freedesktop clients may send `replaces_id` to update an existing notification.
kittyproto honors that normally, but `notifs.toml` can force selected apps to
create a fresh notification instead:

```toml
[replacement]
force_new_apps = ["tauon"]
```

Matching is case-insensitive and substring-based against the notification
`app_name`, so `tauon` also matches names such as `Tauon Music Box`.

This keeps replacement semantics intact for progress/status notifications from
other applications while allowing successive Tauon track changes to stack.


## v0.6 album art

Toasts now attempt to display local artwork when a notification provides it via one of:

- `image-path` / `image_path` hint
- `app_icon` as a local file path or `file://` URI
- raw `image-data` / `image_data` / `icon_data` hints

This is aimed at music notifications such as Tauon/MPD track changes.

The default replacement override list now includes both `tauon` and `mpd`.


## v0.7 artwork renderer

- Removed the application-name line from toast UI.
- Raw Freedesktop `image-data` is preferred over file paths and converted to PNG.
- Album art is sent directly with kitty's graphics protocol instead of spawning `icat`.
- This avoids TTY contention between the toast TUI and the icat kitten.


## v0.8 layout tuning

Album-art/icon placement and text placement can now be tuned directly in `notifs.toml`:

```toml
[toast]
art_width_cols = 11
art_height_rows = 5
art_x_col = 3
art_y_row = 2
art_gap_cols = 2
text_x_offset_cols = 0
text_y_row = 2
```

`art_width_cols` / `art_height_rows` are the icon/album-art size controls.
