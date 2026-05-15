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
# `dconf update` at post_install to regenerate /etc/dconf/db/local. The
# local profile is wired in /etc/dconf/profile/user (shipped by base GNOME
# packages — chain is `user-db:user` then `system-db:local`) and overlays
# the user dconf db with our defaults. Users can still override any
# individual key via Settings / extension prefs / gsettings — system db
# only supplies the DEFAULTS users see on first session.
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
