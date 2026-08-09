# kittyproto launcher — Rev 5.5

Place this directory at:

    ~/.config/kittyproto/launcher/

Rev 5.5 deliberately stops treating the launcher like a layer-shell panel. It is
now a normal kitty OS window so Hyprland can provide the real native window
border, gradient, rounding, shadow and window animation.

## What changed

- Rolled back the Rev 5.4 graphics/background experiment.
- Restored the fast text renderer from the working Rev 5.3 path.
- Removed the terminal-drawn outer box; Hyprland is the only outer border now.
- Launcher runs as a normal kitty window with Wayland app-id/class
  `kittyproto-launcher`.
- Kitty window is 64 columns x 13 rows by default with no internal window
  padding.
- `start-launcher.sh` acts as a basic toggle: run it again while the launcher is
  open and it closes the existing launcher.
- Fuzzy/typo search, usage + recency ranking, hidden apps and Alt+H are unchanged.

## Hyprland rule

Add the contents of `hyprland-rule.lua` alongside your other Hyprland window
rules:

```lua
hl.window_rule({
    name = "kittyproto-launcher",
    match = {
        class = "^kittyproto-launcher$",
    },
    float = true,
    center = true,
})
```

Because this is a regular floating window, your normal Hyprland
`general:border_size`, `general:col.active_border`, decoration rounding,
shadows, blur and window animations can apply normally. Do not copy those
values into the launcher.

## Run

    ./start-launcher.sh

Suggested Hyprland bind target:

    ~/.config/kittyproto/launcher/start-launcher.sh

## Controls

- Type: search
- Up/Down: select
- Enter: launch
- Esc: close
- Ctrl+U: clear query
- Alt+H: hide selected app

## Debug parser

    ./launcher.py --dump | head -30
