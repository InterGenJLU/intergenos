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
#   /usr/lib/systemd/user/intergen-firstboot.service     (systemd user unit)
#
# Autostart mechanism is a systemd user unit (NOT an XDG /etc/xdg/autostart
# .desktop entry). Background: GNOME 49 + systemd 259 both silently skip
# .desktop files carrying X-GNOME-Autostart-Phase= -- systemd-xdg-autostart-
# generator literally prints "GNOME startup phases are handled separately.
# Skipping." (man systemd-xdg-autostart-generator) and gnome-session 49
# logs "no longer manages session services" for any such file. The prior
# .desktop autostart mechanism worked under pre-49 GNOME but is dead-on-
# arrival on the current stack. The user-unit replacement is the canonical
# modern-GNOME-on-systemd migration path per systemd.io DESKTOP_ENVIRONMENTS
# and matches the pattern Fedora + Ubuntu use for first-login services.
#
# The systemd unit declares:
#   * ConditionPathExists=!%h/.local/share/intergen/firstboot-animation-done
#     -- the once-per-user gate, declarative + visible in systemctl status.
#   * Environment=GSK_RENDERER=cairo -- fleet parity with the welcomer
#     wrapper's GSK_RENDERER override for the Mesa ZINK Vulkan crash class
#     on virtualized GPUs; the firstboot DrawingArea+Cairo render is
#     software-rendered at the cairo layer anyway so the override has zero
#     perf cost on real hardware while protecting virtualized targets.
#   * Type=oneshot + ExecStartPost= -- ExecStartPost only runs after a
#     successful ExecStart (rc=0), so a crashed animation will NOT write
#     the done-marker and the next login retries (matches the test-plan
#     section 4.6 failure-resilience criterion).
#   * Before=app-intergen\x2dwelcome@autostart.service -- deterministic
#     chain ordering with the welcomer (whose auto-generated systemd unit
#     name follows the systemd-xdg-autostart-generator convention); the
#     ordering works without requiring any change to the welcomer package.
#   * WantedBy=graphical-session.target -- canonical anchor for first-
#     login user-session services on modern GNOME-on-systemd.
#
# Coexistence with legacy C/DRM sources at assets/intergen-firstboot{,-drm}/
# is unchanged. The Python rewrite already cleared the smoothness QA hard
# gate on three viewing surfaces with the operator-explicit "Python is a
# go" closure; the C bundle remains in tree as documented fallback per Q6
# of the chain-vs-phase walkthrough until eventual cleanup commit.

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
    local userunitdir="${DESTDIR}/usr/lib/systemd/user"

    install -dm755 "${libexec}" "${userunitdir}"

    install -m755 intergen-firstboot.py "${libexec}/intergen-firstboot.py"

    cat > "${userunitdir}/intergen-firstboot.service" <<'UNIT'
[Unit]
Description=InterGenOS first-login branded ECG-pulse animation
Documentation=https://github.com/InterGenJLU/intergenos
ConditionPathExists=!%h/.local/share/intergen/firstboot-animation-done
Before=app-intergen\x2dwelcome@autostart.service
PartOf=graphical-session.target

[Service]
Type=oneshot
Environment=GSK_RENDERER=cairo
ExecStart=/usr/bin/python3 /usr/libexec/intergen-firstboot/intergen-firstboot.py
ExecStartPost=/bin/sh -c "mkdir -p %h/.local/share/intergen && touch %h/.local/share/intergen/firstboot-animation-done"
RemainAfterExit=no

[Install]
WantedBy=graphical-session.target
UNIT
    chmod 644 "${userunitdir}/intergen-firstboot.service"
}

post_install() {
    set -e
    # systemctl --global enable creates the install-time symlink at
    # /etc/systemd/user/graphical-session.target.wants/; per-user systemd
    # instances pick up the new unit when each user logs in. No
    # daemon-reload is needed (--global is not a valid scope for
    # daemon-reload, which operates on a running systemd instance).
    systemctl --global enable intergen-firstboot.service 2>/dev/null || true
}
