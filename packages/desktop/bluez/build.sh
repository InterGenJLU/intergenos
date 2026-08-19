#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# bluez 5.86 — Bluetooth protocol stack
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i '4967,4968d' src/adapter.c
    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --localstatedir=/var \
                --enable-library \
                --disable-manpages
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Convenience symlink for bluetoothd
    ln -svf ../libexec/bluetooth/bluetoothd "${DESTDIR}/usr/sbin/bluetoothd"
}

post_install() {
    set -e
    # Enable the bluetooth service.
    #
    # Unmasked, and the unit is named in full. `systemctl enable` is an
    # offline file operation: measured 2026-08-19 in a chroot built from this
    # systemd 259.1, enabling a PRESENT unit returns 0 and writes the symlink,
    # a repeat call returns 0, and the only reachable failure is a unit that
    # does not exist, which returns 1. This package installs bluetooth.service
    # itself, so a non-zero means its own unit is missing.
    #
    # The suffix is spelled out because that is the exact string
    # intergenos-base-files' 80-intergenos-enable.preset whitelists; the bare
    # name resolved to the same unit (measured), but a recipe and the preset
    # that has to agree with it should not be written two different ways.
    systemctl enable bluetooth.service
}
