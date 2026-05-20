#!/bin/bash
# intergen-firstboot 1.0 -- InterGenOS first-login branded animation
#
# Wraps the GTK4/Wayland Python animation at
# assets/intergen-firstboot-py/intergen-firstboot.py from the repo
# into a proper installable package.
#
# The animation:
#   * Renders the ECG heartbeat pulse animation as a fullscreen Wayland
#     window via Cairo + PangoCairo, with GtkFrameClock-paced vsync.
#   * Runs IN the user's session post-GDM-login (not pre-compositor),
#     fades to black on completion, and exits so the welcomer takes
#     over with the desktop already painted underneath.
#   * Runs once per new user account, state-tracked via a done-marker
#     at ~/.local/share/intergen/firstboot-animation-done.
#   * Math is ported VERBATIM from the C source at
#     assets/intergen-firstboot/{pulse,text}.c. Visual properties
#     (sweep count, timing, ECG curve, text content, font, fade easings)
#     are LOCKED design properties per operator-direct 2026-05-20T~19:Z.
#
# Source layout (in the tarball):
#   if-pkg/intergen-firstboot.py   (the Python animation app)
#
# Install layout:
#   /usr/libexec/intergen-firstboot/intergen-firstboot.py
#   /usr/bin/intergen-firstboot                          (shell wrapper)
#   /etc/xdg/autostart/intergen-firstboot.desktop        (autostart entry)
#
# The autostart .desktop uses X-GNOME-Autostart-Phase=Initialization so
# the animation fires BEFORE the welcomer's default Applications phase.
# Filename-sort (intergen-firstboot.desktop < intergen-welcome.desktop)
# provides belt-and-suspenders ordering.
#
# The wrapper script gates execution on the done-marker, exits cleanly
# if already complete, and writes the done-marker only after the Python
# app exits with status 0. If the animation crashes mid-render, the next
# login attempts it again -- consistent with the test-iteration UX
# captured in docs/research/firstboot/test-plan.md.
#
# This package coexists with the legacy C/DRM sources at
# assets/intergen-firstboot{,-drm}/ until smoothness testing on real
# hardware locks the Python implementation per Q4 of the chain-vs-phase
# walkthrough. Until that verdict, the C source remains in tree as the
# documented fallback per Q6.

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    local libexec="${DESTDIR}/usr/libexec/intergen-firstboot"
    local bindir="${DESTDIR}/usr/bin"
    local autostartdir="${DESTDIR}/etc/xdg/autostart"

    install -dm755 "${libexec}" "${bindir}" "${autostartdir}"

    install -m755 intergen-firstboot.py "${libexec}/intergen-firstboot.py"

    cat > "${bindir}/intergen-firstboot" <<'WRAPPER'
#!/bin/bash
# intergen-firstboot launcher -- once-per-user gate.
done_marker="${HOME}/.local/share/intergen/firstboot-animation-done"
if [ -e "${done_marker}" ]; then
    exit 0
fi

python3 /usr/libexec/intergen-firstboot/intergen-firstboot.py "$@"
rc=$?
if [ "${rc}" -eq 0 ]; then
    mkdir -p "$(dirname "${done_marker}")" || true
    touch "${done_marker}" || true
fi
exit "${rc}"
WRAPPER
    chmod 755 "${bindir}/intergen-firstboot"

    cat > "${autostartdir}/intergen-firstboot.desktop" <<'AUTOSTART'
[Desktop Entry]
Type=Application
Name=InterGenOS First Boot Animation
Comment=Branded first-login animation that fades to the welcomer
Exec=intergen-firstboot
OnlyShowIn=GNOME;
StartupNotify=false
NoDisplay=true
X-GNOME-Autostart-Phase=Initialization
AUTOSTART
}
