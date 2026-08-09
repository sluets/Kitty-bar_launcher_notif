# kittyproto bar Rev 4.1 — spacing controls

Drop these two files into `~/.config/kittyproto/`.

This revision keeps Rev 4 behavior and adds tunable visual spacing / mouse hitbox controls.

Key settings in `theme.toml`:

```toml
[workspaces]
padding_cells = 0
gap_cells = 1

[media]
gap_cells = 2
click_padding_cells = 0

[volume]
click_padding_cells = 1

[status]
gap_cells = 0
clock_gap_cells = 1

[network]
click_padding_cells = 1

[bluetooth]
click_padding_cells = 1
```

`click_padding_cells` adds blank terminal cells around the module *inside its click region*. This makes Wi-Fi/Bluetooth/volume easier to hit without needing a large separate gap.

After replacing the files, restart the bar:

```bash
~/.config/kittyproto/stop-bar.sh
~/.config/kittyproto/start-bar.sh DP-2
```
