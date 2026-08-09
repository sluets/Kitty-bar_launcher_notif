# kittyproto sync hardening v2

Replace only:

```text
~/.config/kittyproto/launch.py
```

This keeps the working border-width startup fix and adds the same defensive
validation to Hyprland rounding sync.

Observed startup behavior:

```text
radius=1000
```

The bar now accepts compositor-synced radius values only from 0 through half
the configured bar height. With the current 34px bar, valid synced rounding is
0..17px. Anything outside that range falls back to `[bar].radius_px` from
`theme.toml`.

The existing `kittyproto resolved chrome:` logging remains in place.
# Kitty-bar_launcher_notif
# Kitty-bar_launcher_notif
