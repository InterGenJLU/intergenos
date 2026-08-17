#!/bin/sh
# InterGenOS — readable GDM greeter on HiDPI displays that expose no EDID.
#
# GNOME scales the GDM greeter from the EDID-derived physical DPI. Virtual
# GPUs (VirtualBox VMSVGA/vmwgfx, QEMU) and the occasional bare-metal panel
# report NO EDID, so on a 4K display GNOME falls back to 96 DPI / scale 1 and
# the greeter (clock, the username field, top-bar) renders unreadably small.
# A 4K-in-a-VM install — increasingly the first thing a new user tries — hits
# this, and so does any 4K machine whose panel ships absent/bad EDID.
#
# This forces a 2x greeter scale ONLY when a connected display is >= 3840 px
# wide AND exposes no EDID. EDID-bearing displays keep GNOME's native
# auto-scale (so we never double-scale them); sub-4K displays are untouched
# (so 1080p/1440p greeters are never bloated). The decision is written to a
# dedicated gdm dconf fragment and compiled with `dconf update`.
#
# Delivery: this runs as a gdm.service ExecStartPre (see the
# 10-intergenos-greeter-hidpi.conf drop-in), i.e. SYNCHRONOUSLY inside gdm's
# own startup, immediately before the greeter launches and reads the dconf db.
# It is deliberately NOT a separate `Before=gdm` oneshot: that inter-unit
# ordering was the InterGenOS G3-3 per-resolution selector that caused the
# AMD first-boot GDM SIGABRT (PI-14, operator-proven) and was retired. An
# ExecStartPre has no cross-unit ordering race, and this script is fail-safe
# (always exits 0) so it can never block the greeter from starting.
set -u

FRAG=/etc/dconf/db/gdm.d/10-intergenos-greeter-hidpi
want=1

for st in /sys/class/drm/card*-*/status; do
    [ -r "$st" ] || continue
    [ "$(cat "$st" 2>/dev/null)" = connected ] || continue
    d=${st%/status}
    # First line of modes is the connector's preferred (largest) mode, "WxH".
    read -r first < "$d/modes" 2>/dev/null || continue
    w=${first%x*}
    edid=0
    [ -s "$d/edid" ] && edid=1
    [ "${w:-0}" -ge 3840 ] && [ "$edid" -eq 0 ] && want=2
done

if [ "$want" -eq 2 ]; then
    printf '[org/gnome/desktop/interface]\nscaling-factor=uint32 2\n' > "$FRAG"
else
    rm -f "$FRAG"
fi

dconf update 2>/dev/null || true
exit 0
