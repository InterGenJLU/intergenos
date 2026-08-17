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
    systemctl enable NetworkManager.service 2>/dev/null || true

    # Disable systemd-networkd if enabled (conflicts with NM)
    systemctl disable systemd-networkd.service 2>/dev/null || true
    systemctl disable systemd-networkd-wait-online.service 2>/dev/null || true

    # Enable NetworkManager-wait-online so network-online.target fires once
    # NM has a connection. Without this target firing, systemd-timesyncd
    # never wakes from "Idle." and the system clock never syncs to NTP
    # (operator-flagged 2026-05-25: install-laptop showed Feb 6 because
    # the chain was broken). wait-online has a default 30s timeout; on a
    # network-less boot it falls through and the rest of boot continues,
    # so the prior comment about "blocks boot indefinitely" was overly
    # cautious — worst case is +30s, common case is +<5s.
    systemctl enable NetworkManager-wait-online.service 2>/dev/null || true
}
