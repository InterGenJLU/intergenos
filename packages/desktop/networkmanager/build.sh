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

# No post_install hook. Default enablement of every unit this package ships is
# decided in one place — intergenos-base-files'
# /usr/lib/systemd/system-preset/80-intergenos-enable.preset — and applied by the
# `systemctl preset-all` pass the image build and the installer both run. A
# `systemctl enable` here was a second voice for the same decision and the preset
# pass reverted it, so the tree stated one default and shipped another. Decided
# 2026-08-19: the preset files own this; recipes do not enable their own units.
