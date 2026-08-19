#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rtkit 0.13 — RealtimeKit D-Bus service for real-time scheduling
# Required by PipeWire and GNOME Shell for real-time thread scheduling

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup ..                \
          --prefix=/usr           \
          --libdir=/usr/lib       \
          --buildtype=release     \
          -Dinstalled_tests=false \
          -Dlibsystemd=enabled
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
}

post_install() {
    set -e
    # rtkit user/group is declared by /usr/lib/sysusers.d/rtkit.conf
    # and created by the pkm canonical sysusers hook before this
    # lifecycle hook runs.

    # Enable the realtime-scheduling broker.
    #
    # Unmasked. `systemctl enable` is an offline file operation: measured
    # 2026-08-19 in a chroot built from this systemd 259.1, enabling a PRESENT
    # unit returns 0 and writes the symlink, a repeat call returns 0, and the
    # only reachable failure is a unit that does not exist, which returns 1.
    # This package installs rtkit-daemon.service itself, so a non-zero means
    # its own unit is missing.
    #
    # KNOWN GAP: rtkit-daemon.service is not whitelisted in
    # intergenos-base-files' 80-intergenos-enable.preset, so the preset policy
    # resolves it to `disable` through the 99- catch-all. Measured on an
    # installed system 2026-08-19: the PRESET column of `systemctl
    # list-unit-files rtkit-daemon.service` reads disabled, the unit's STATE is
    # disabled, and the daemon is nevertheless running — the package ships
    # /usr/share/dbus-1/system-services/org.freedesktop.RealtimeKit1.service,
    # so it is D-Bus-activated and does not need the WantedBy symlink. So this
    # recipe's enable and the preset policy disagree, and unmasking settles
    # only whether a FAILING enable is visible. Which of the two should win is
    # a default-running-service decision, not a recipe fix.
    systemctl enable rtkit-daemon.service
}
