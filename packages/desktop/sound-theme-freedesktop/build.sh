#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# sound-theme-freedesktop 0.8 — Default XDG sound theme
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # INTERNVL-PI-A: GNOME ships libcanberra-login-sound.desktop, whose autostart
    # plays the XDG "desktop-login" event at session login
    # (canberra-gtk-play --id desktop-login). sound-theme-freedesktop 0.8 ships
    # service-login.oga (the *system* login event) but NOT desktop-login.oga, so
    # the autostart fails "File or data not found" and the login jingle never
    # plays. desktop-login is the spec-correct event for a desktop-environment
    # login, so provide it here by reusing the freedesktop login chime
    # (service-login.oga) for the desktop-login event. Fail loud if the source
    # event is missing rather than ship a broken reference (Rule 21).
    local theme="${DESTDIR}/usr/share/sounds/freedesktop"
    local staged=0 sub src
    for sub in stereo mono; do
        src="${theme}/${sub}/service-login.oga"
        if [ -f "$src" ]; then
            cp -a "$src" "${theme}/${sub}/desktop-login.oga"
            staged=1
        fi
    done
    if [ "$staged" -ne 1 ]; then
        echo "FATAL: no service-login.oga in sound-theme-freedesktop to source desktop-login.oga from" >&2
        return 1
    fi
}
