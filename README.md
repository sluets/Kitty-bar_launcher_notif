# kittyproto opacity fix v1

Replace only:

```text
~/.config/kittyproto/bar.py
```

Cause:
The temporary chrome-stability change redrew the semi-transparent background
image several times without removing the previous kitty graphics placement.
Kitty blends overlapping semi-transparent images, so repeated 0.88-opacity
images rapidly became effectively opaque.

Fix:
The bar now draws exactly one chrome image per bar process again.

The working fixes in `launch.py` for Hyprland startup border/radius validation
are untouched.

Restart both bars:

```bash
~/.config/kittyproto/stop-bar.sh
~/.config/kittyproto/start-bar.sh DP-1
~/.config/kittyproto/start-bar.sh DP-2
```
