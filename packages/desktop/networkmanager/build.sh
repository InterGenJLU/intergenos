#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# networkmanager 1.56.0 — Network connection manager
# BLFS 13.0

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
          -Dnmtui=true \
          -Dovs=false \
          -Dppp=false \
          -Dselinux=false \
          -Dqt=false \
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

    # Drop initrd-variant unit files. Upstream ships these for distros
    # that run NetworkManager inside a runtime-generated initramfs
    # (remote-root via DHCP). InterGenOS does NOT — our live-ISO
    # initramfs uses busybox + a custom init.sh, and dracut/mkinitcpio
    # are RATIFIED-AGAINST for any initramfs path; networking is brought
    # up post-pivot by NM proper. Shipping the *-initrd.service units in
    # the final root causes a D-Bus name collision on
    # org.freedesktop.NetworkManager — systemd refuses to load
    # either unit ("Two services allocated for the same bus name"), NM
    # stays dead at boot, GNOME's panel applet shows "Network unavailable"
    # while systemd-networkd silently does the actual work.
    rm -f "${DESTDIR}/usr/lib/systemd/system/NetworkManager-initrd.service"
    rm -f "${DESTDIR}/usr/lib/systemd/system/NetworkManager-config-initrd.service"
    rm -f "${DESTDIR}/usr/lib/systemd/system/NetworkManager-wait-online-initrd.service"
}

post_install() {
    set -e
    # Enable NetworkManager for GNOME desktop integration
    # (replaces systemd-networkd which is server-oriented)
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
