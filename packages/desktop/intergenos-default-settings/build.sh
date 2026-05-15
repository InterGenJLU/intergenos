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
    install -dm755 "${DESTDIR}/etc/dconf/db/local.d"
    install -m644 ./01-intergenos-defaults \
        "${DESTDIR}/etc/dconf/db/local.d/01-intergenos-defaults"
}

post_install() {
    set -e
    # Regenerate /etc/dconf/db/local from local.d/*. Idempotent — safe to
    # call unconditionally. The compiled local db is what users actually
    # read; without this step the keyfile is dormant.
    dconf update 2>/dev/null || true
}
