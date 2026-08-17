#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# dbus-pass2 1.16.2 — Message bus (pass 2 with doxygen API docs + AppArmor)
# Rebuilds dbus with -Ddoxygen_docs=enabled and -Dapparmor=enabled now that
# doxygen and apparmor are available (both tier-after-ch8). Supersedes the
# pass 1 (tier:core, ch8) build at install time via migrate-pkm-supersedes.sh.

configure() {
    set -e
    mkdir -p build
    cd build
    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --sysconfdir=/etc   \
          --localstatedir=/var \
          --buildtype=release \
          -Ddoxygen_docs=enabled \
          -Dapparmor=enabled
}

build() {
    set -e
    cd build
    ninja
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Set the launch helper to the upstream/BLFS-declared 4750 — the SAME
    # discipline as packages/core/dbus/build.sh (pass1). Pass2 deploys AFTER
    # pass1, so whatever this recipe stages is what the system ships; without
    # this line the helper rode meson's un-pinned staging mode (4110 — an
    # ACCIDENT, caught by the 4.76 setuid inventory gate on its first firing,
    # 2026-07-10). Decided: set what upstream declares so no unknown
    # reader path breaks and nothing diverges silently (PRIME DIRECTIVE);
    # the verified-tighter 4110 exec-only variant is banked as a researched
    # hardening candidate, not a ride-along.
    chmod 4750 "${DESTDIR}/usr/libexec/dbus-daemon-launch-helper"
}

post_install() {
    set -e
    # Identical to pass1 (packages/core/dbus/build.sh): the messagebus
    # user/group must exist before the chown, and the chown clears the
    # setuid bit on a regular file (kernel behavior) — restore 4750 AFTER.
    systemd-sysusers /usr/lib/sysusers.d/dbus.conf
    chown root:messagebus /usr/libexec/dbus-daemon-launch-helper
    chmod 4750 /usr/libexec/dbus-daemon-launch-helper
}
