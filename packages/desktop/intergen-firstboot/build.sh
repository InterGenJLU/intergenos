#!/bin/bash
# intergen-firstboot 1.0 -- InterGenOS first-login branded ECG animation
# (GNOME Shell extension)
#
# Renders the operator-tuned 22.4s ECG pulse + text sequence above gnome-
# shell's session-startup UI on the first login per user. Architecture is
# a GNOME Shell extension (St.DrawingArea + Clutter.Timeline + Main.layout
# Manager.addTopChrome). Math ported VERBATIM from the canonical C sources
# at assets/intergen-firstboot/{pulse,text}.{c,h} via the Python reference
# at assets/intergen-firstboot-py/intergen-firstboot.py.
#
# Why a shell extension (not a systemd-user-unit Python app like v2-v5):
# during the v1-v5 iteration arc, every Wayland-client-protocol mechanism
# on Mutter empirically failed at one or more layers:
#   v1 .desktop autostart -- silently dropped by systemd 259 + gnome-session 49
#   v2 systemd user unit  -- GTK init failure path + ordering issues (resolved v3)
#   v3 v2 + init-failure propagation -- Mutter ignored fullscreen() on toplevel
#   v4 wlr-layer-shell OVERLAY -- did NOT defeat gnome-shell compositor-drawn UI
#   v5 ext-session-lock-v1 -- Mutter does NOT implement this protocol
# Only the gnome-shell-extension layer renders above ALL client surfaces AND
# compositor-drawn UI (activities overview + top bar + dock). v6 lands here.
#
# Q5 design properties are LOCKED per operator-direct 2026-05-20T~19:Z: sweep
# count, total duration, sweep rate, ECG curve shape, text content + fade
# curves, font choice, fade-easing are NON-NEGOTIABLE. Math constants port
# verbatim to extension.js; the rendering MECHANISM (DRM/KMS -> GTK4/Wayland
# -> JS+St.DrawingArea) is the only thing that changes across iterations.
#
# Source layout: assets/intergen-firstboot/intergen-firstboot@intergenos.org/
# in the canonical repo. Install layout:
#   /usr/share/gnome-shell/extensions/intergen-firstboot@intergenos.org/
#     extension.js
#     metadata.json
#
# Default-enabled via config/gsettings/91_intergenos-extensions.gschema.override
# per D-006 SSoT -- the intergenos-default-settings gschema-override package
# is the canonical source for default-enabled extensions. The package itself
# does NOT enable; the gschema-override declares enabled-extensions=[...].
#
# Pairs with packages/desktop/intergen-no-overview/ which suppresses the
# activities overview at every login (separate parallel deliverable per
# operator-direct 2026-05-21T~01:Z).
#
# Coexistence with legacy C/DRM sources at assets/intergen-firstboot{,-drm}/
# is unchanged. The Python reference at assets/intergen-firstboot-py/ stays
# in tree as the canonical math reference per Q6 fallback preservation.

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

    # Copy extension files directly from canonical source. Same pattern as
    # packages/desktop/intergen-pkm-notifier/build.sh -- first-party authored
    # content lives in /mnt/intergenos/ + ships via cp -a without tarball
    # assembly.
    local ext_dir="${DESTDIR}/usr/share/gnome-shell/extensions/intergen-firstboot@intergenos.org"
    install -dm755 "${ext_dir}"
    cp -a /mnt/intergenos/assets/intergen-firstboot/intergen-firstboot@intergenos.org/extension.js \
        "${ext_dir}/extension.js"
    cp -a /mnt/intergenos/assets/intergen-firstboot/intergen-firstboot@intergenos.org/metadata.json \
        "${ext_dir}/metadata.json"
    chmod 644 "${ext_dir}/extension.js" "${ext_dir}/metadata.json"
}

post_install() {
    set -e
    # No glib-compile-schemas, no systemctl --global enable. Default-enabled
    # state is set by the intergenos-default-settings gschema-override
    # package per D-006 SSoT. The extension activates automatically when
    # gnome-shell loads its enabled-extensions list at session start.
    :
}
