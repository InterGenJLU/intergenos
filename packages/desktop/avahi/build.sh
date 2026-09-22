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

    # AppArmor: usr.sbin.avahi-daemon confines
    # /usr/sbin/avahi-daemon, which this package installs. The profile
    # text is upstream's, staged by the apparmor package in
    # /usr/share/apparmor/extra-profiles, which nothing loads; this package links
    # it into /etc/apparmor.d because this package is what puts the program on
    # the machine, and the apparmor unit loads every file in /etc/apparmor.d at
    # boot. apparmor is a runtime dependency so the link cannot dangle. See
    # packages/core/apparmor/README.md, "Where a profile lives".
    install -dm755 "${DESTDIR}/etc/apparmor.d"
    for profile in usr.sbin.avahi-daemon; do
        ln -s "../../usr/share/apparmor/extra-profiles/${profile}" \
            "${DESTDIR}/etc/apparmor.d/${profile}"
    done
}

# No post_install hook. Default enablement of every unit this package ships is
# decided in one place — intergenos-base-files'
# /usr/lib/systemd/system-preset/80-intergenos-enable.preset — and applied by the
# `systemctl preset-all` pass the image build and the installer both run. A
# `systemctl enable` here was a second voice for the same decision and the preset
# pass reverted it, so the tree stated one default and shipped another. Decided
# 2026-08-19: the preset files own this; recipes do not enable their own units.
