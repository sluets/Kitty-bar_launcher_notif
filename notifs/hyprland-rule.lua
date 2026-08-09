-- kittyproto notification slots (v0.4)
-- Replace the old single ^kittyproto-notif$ rule with these fixed slots.
-- The daemon reflows notifications by changing slot CONTENT, not by moving windows.

local kittyproto_notif_y = { 70, 190, 310, 430 }

for i, y in ipairs(kittyproto_notif_y) do
    local slot = i - 1
    hl.window_rule({
        name = "kittyproto-notif-" .. slot,
        match = {
            class = "^kittyproto-notif-" .. slot .. "$",
        },

        float = true,
        pin = true,
        no_initial_focus = true,

        size = { 420, 110 },
        move = { 2110, y },
    })
end
