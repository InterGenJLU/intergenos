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

# No post_install hook.
#
# This recipe's post_install existed only to run `systemctl enable bluetooth.service`.
# That default is decided in intergenos-base-files'
# /usr/lib/systemd/system-preset/80-intergenos-enable.preset
# and applied by the `systemctl preset-all` the image build and the installer both
# run; measured 2026-08-19 against that same engine (`systemctl --root <root>
# preset-all` over the tree's own preset files), the policy resolves bluetooth.service
# to ENABLED, so the call changed nothing on a fresh install.
#
# What it changed was an upgrade: pkm fires a sealed post_install on every upgrade
# and nothing re-runs preset-all afterwards, so a user who had turned the unit off
# got it back on with no message. With the call gone the function had nothing left
# to do, and a hook that does nothing is not kept for symmetry — it is removed, so
# nothing fires on every install and upgrade to accomplish nothing. Decided 2026-08-19.
