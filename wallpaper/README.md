# kittyproto wallpaper picker — r7

5x5 thumbnail-only wallpaper picker for kitty.

## r7 fixes

- Window sizing now matches the real thumbnail grid. The r6 launcher still reserved the old +2-cell selector gutter even though wallpaper.py no longer used it.
- Selector is no longer made from terminal box-drawing characters.
- Selector is now a transparent Kitty graphics-protocol overlay on top of the selected thumbnail, so it consumes no terminal cells and does not disturb grid coordinates.
- `tile_gap_x_cells = 0` / `tile_gap_y_cells = 0` means actual thumbnail rectangles touch. Negative gaps are still supported.
- Keeps the random-order toggle and current-wallpaper tracking.
