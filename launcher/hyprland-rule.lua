-- kittyproto launcher: normal floating kitty window.
-- Hyprland supplies the native border/rounding/shadow/animation from your
-- existing global window settings. No custom border values are needed here.
hl.window_rule({
    name = "kittyproto-launcher",
    match = {
        class = "^kittyproto-launcher$",
    },
    float = true,
    center = true,
})
