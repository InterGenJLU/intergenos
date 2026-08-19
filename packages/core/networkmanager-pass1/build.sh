#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# networkmanager-pass1 1.56.0 — NetworkManager (bootstrap, no desktop integration)
# First pass of 2-pass build. Disables iptables, polkit, bluez, introspection,
# vala, nmtui, modem_manager — anything that pulls tier:desktop deps.
# System networking only: bring up wired + WPA-supplicant WiFi at boot.

configure() {
    set -e
    # Fix Python scripts that reference python2
    grep -rl '^#!.*python$' | xargs sed -i '1s/python/&3/' 2>/dev/null || true

    mkdir -p build
    cd    build

    meson setup ..            \
          --prefix=/usr       \
          --libdir=/usr/lib   \
          --buildtype=release \
          -Dlibaudit=no \
          -Dmodem_manager=false \
          -Dnm_cloud_setup=false \
          -Dnbft=false \
          -Dnmtui=false \
          -Dovs=false \
          -Dppp=false \
          -Dselinux=false \
          -Dqt=false \
          -Diptables=/usr/bin/false \
          -Dpolkit=false \
          -Dintrospection=false \
          -Dvapi=false \
          -Dsession_tracking=systemd \
          -Dtests=no \
          -Ddocs=false
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
    # Enable NetworkManager for system networking at boot. (Full NM with
    # desktop integration supersedes this pass1 at install time; the
    # systemctl enable persists across the supersede.)
    #
    # Unmasked. `systemctl enable` is an offline file operation, so it does
    # its work without a running manager: measured 2026-08-19 in a chroot
    # built from this systemd 259.1, enabling a PRESENT unit returns 0 and
    # writes the symlink both with and without /proc mounted, and a second
    # call returns 0 unchanged. The only reachable failure is a unit that
    # does not exist, which returns 1. This package installs both
    # NetworkManager.service and NetworkManager-wait-online.service itself
    # (both appear in its own pkm file manifest), so a non-zero here means a
    # unit this recipe just installed is missing — a condition that must stop
    # the build rather than ship a service that is silently not enabled.
    systemctl enable NetworkManager.service

    # Disable systemd-networkd, which conflicts with NM.
    #
    # Unmasked, and deliberately not guarded on unit presence: the systemd
    # recipe passes no networkd opt-out, so systemd ships both units
    # unconditionally, and systemd is a declared build AND runtime dependency
    # of this package — there is no variant in this tree where these units are
    # legitimately absent. Disabling a present unit returns 0 whether or not
    # it was enabled (measured 2026-08-19), so a non-zero here has no benign
    # reading.
    systemctl disable systemd-networkd.service
    systemctl disable systemd-networkd-wait-online.service

    # Enable NetworkManager-wait-online so network-online.target fires once
    # NM has a connection. Without this target firing, systemd-timesyncd
    # never wakes from "Idle." and the system clock never syncs to NTP
    # (operator-flagged 2026-05-25: install-laptop showed Feb 6 because
    # the chain was broken). wait-online has a default 30s timeout; on a
    # network-less boot it falls through and the rest of boot continues,
    # so the prior comment about "blocks boot indefinitely" was overly
    # cautious — worst case is +30s, common case is +<5s.
    #
    # Unmasked for the same measured reason as NetworkManager.service above;
    # this package installs this unit too.
    #
    # KNOWN GAP, recorded here because the enable alone does not settle the
    # effect described above: NetworkManager-wait-online.service is not
    # whitelisted in intergenos-base-files' 80-intergenos-enable.preset, so the
    # preset policy resolves it to `disable` through the 99- catch-all.
    # Measured on an installed system 2026-08-19: the PRESET column of
    # `systemctl list-unit-files NetworkManager-wait-online.service` reads
    # disabled while the unit's STATE reads enabled — the recipe's enable is
    # what survived there, and the two disagree. Unmasking settles only whether
    # a FAILING enable is visible. Which of the two should win is a
    # default-running-service decision, not a recipe fix.
    systemctl enable NetworkManager-wait-online.service
}
