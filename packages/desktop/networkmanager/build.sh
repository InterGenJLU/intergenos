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
    #
    # Unmasked. `systemctl enable` is an offline file operation and needs no
    # running manager: measured 2026-08-19 in a chroot built from this systemd
    # 259.1, enabling a PRESENT unit returns 0 and writes the symlink, and a
    # repeat call returns 0 unchanged; the only reachable failure is a unit
    # that does not exist, which returns 1. This package owns
    # NetworkManager.service (do_install above prunes only the three -initrd
    # units), so a non-zero here means the unit it just installed is missing.
    systemctl enable NetworkManager.service

    # Disable systemd-networkd, which conflicts with NM.
    #
    # Unmasked, and deliberately not guarded on unit presence: the systemd
    # recipe passes no networkd opt-out, so both units ship unconditionally
    # with systemd, which is a declared dependency here — there is no variant
    # in this tree where they are legitimately absent. Disabling a present
    # unit returns 0 whether or not it was enabled (measured 2026-08-19), so a
    # non-zero here has no benign reading.
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
    # this package owns this unit (pkm reports it as owned by networkmanager
    # 1.56.0 on an installed system).
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
