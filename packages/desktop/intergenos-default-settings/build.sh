#!/bin/bash
# intergenos-default-settings 1.0.0 — Curated InterGenOS UX defaults
#
# Ships InterGenOS-curated dconf system-db defaults: GNOME Shell extension
# settings (burn-my-windows, blur-my-shell, bluetooth-quick-connect,
# coverflowalttab) plus system polish (numlock-state, power profile,
# night-light schedule, keybindings). Content sourced from the canonical-
# baseline workstation (2026-05-14).
#
# Mechanism: install a keyfile into /etc/dconf/db/local.d/ + run
# `dconf update` at post_install to regenerate /etc/dconf/db/local. We
# also author /etc/dconf/profile/user (chain `user-db:user` then
# `system-db:local`) so the system local db actually overlays the user
# dconf db — without this profile, dconf does NOT consult the system db
# at all and the keyfile is dormant. dconf upstream ships an empty
# /etc/dconf/profile/ directory; the `user` profile is a distro decision,
# and we own it here because we own what the system db should layer over
# the empty-by-default user db.
#
# Users can still override any individual key via Settings / extension
# prefs / gsettings — system db only supplies the DEFAULTS users see on
# first session.
#
# Real-distro precedent: Linux Mint mint-default-settings, Pop!_OS
# pop-default-settings, elementary elementary-default-settings, Fedora
# fedora-release-workstation — all use the same pattern.
#
# License: GPL-3.0-or-later.

configure() {
    set -e
    # No-op — drop-in keyfile, no compile.
    :
}

build() {
    set -e
    # No-op.
    :
}

do_install() {
    set -e

    # ---- dconf profile (wires the local system-db into user lookups) ---
    # Authored inline because the only sane content is two well-known
    # lines and the tarball doesn't need to carry it. Without this file,
    # dconf reads only the user db and the local.d keyfile below is
    # dormant — defaults never surface to apps.
    install -dm755 "${DESTDIR}/etc/dconf/profile"
    cat > "${DESTDIR}/etc/dconf/profile/user" <<'DCONFPROFILE'
user-db:user
system-db:local
DCONFPROFILE
    chmod 644 "${DESTDIR}/etc/dconf/profile/user"

    # ---- dconf system-db defaults --------------------------------------
    install -dm755 "${DESTDIR}/etc/dconf/db/local.d"
    install -m644 ./01-intergenos-defaults \
        "${DESTDIR}/etc/dconf/db/local.d/01-intergenos-defaults"

    # ---- burn-my-windows file-based profile ----------------------------
    # burn-my-windows v48 stores profile config in
    # ~/.config/burn-my-windows/profiles/<microsecond-id>.conf files —
    # NOT in dconf paths alone. The dconf path
    # /org/gnome/shell/extensions/burn-my-windows/profile-close-0/ is a
    # mirror that the extension's ProfileManager.js generates FROM the
    # .conf files it discovers; the .conf is the authoritative source.
    # Without this file present, the extension generates an empty default
    # profile at first session start ("default settings" appearance even
    # when our dconf defaults are loaded). Shipping via /etc/skel ensures
    # every freshly-created user (including liveuser via init.sh's
    # `cp -a /etc/skel/. /home/liveuser/`) gets the curated profile
    # before the extension's first-run profile-init can write a blank.
    # Filename matches the canonical-baseline workstation's profile ID
    # for byte-level consistency; the extension treats filenames as
    # opaque microsecond-stamped IDs.
    install -dm755 "${DESTDIR}/etc/skel/.config/burn-my-windows/profiles"
    install -m644 ./1775735161994164.conf \
        "${DESTDIR}/etc/skel/.config/burn-my-windows/profiles/1775735161994164.conf"
}

post_install() {
    set -e
    # Regenerate /etc/dconf/db/local from local.d/*. Idempotent — safe to
    # call unconditionally. The compiled local db is what users actually
    # read; without this step the keyfile is dormant.
    dconf update 2>/dev/null || true
}
