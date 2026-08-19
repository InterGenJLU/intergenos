#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# avahi 0.8 — Service Discovery for Linux using mDNS/DNS-SD
# BLFS 13.0

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    # Fix security vulnerability in avahi-daemon (BLFS)
    sed -i '426a if (events & AVAHI_WATCH_HUP) { \
client_free(c); \
return; \
}' avahi-daemon/simple-protocol.c

    ./configure --prefix=/usr        \
                --sysconfdir=/etc    \
                --localstatedir=/var \
                --disable-static     \
                --disable-libevent   \
                --disable-mono       \
                --disable-monodoc    \
                --disable-python     \
                --disable-qt3        \
                --disable-qt4        \
                --disable-qt5        \
                --disable-gtk        \
                --disable-gtk3       \
                --enable-core-docs   \
                --with-distro=none   \
                --with-dbus-system-address='unix:path=/run/dbus/system_bus_socket'
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}

post_install() {
    set -e
    # avahi user/group + netdev group are declared by
    # /usr/lib/sysusers.d/avahi.conf and created by the pkm canonical
    # sysusers hook before this lifecycle hook runs.

    # Enable the mDNS/DNS-SD responder.
    #
    # Unmasked. `systemctl enable` is an offline file operation: measured
    # 2026-08-19 in a chroot built from this systemd 259.1, enabling a PRESENT
    # unit returns 0 and writes the symlink, a repeat call returns 0, and the
    # only reachable failure is a unit that does not exist, which returns 1.
    # This package installs avahi-daemon.service itself, so a non-zero means
    # its own unit is missing.
    #
    # KNOWN GAP: avahi-daemon.service is not whitelisted in
    # intergenos-base-files' 80-intergenos-enable.preset, so the preset policy
    # resolves it to `disable` through the 99- catch-all. Measured on an
    # installed system 2026-08-19: the PRESET column of `systemctl
    # list-unit-files avahi-daemon.service` reads disabled while the unit's
    # STATE reads enabled — this recipe's enable is what survived there, and
    # the two disagree. Unmasking settles only whether a FAILING enable is
    # visible. Which of the two should win is a default-running-service
    # decision, not a recipe fix.
    systemctl enable avahi-daemon.service
}
