#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# D-Bus 1.16.2
# LFS 13.0 Section 8.79
#
# Uses meson. DESTDIR supported.

configure() {
    set -e
    mkdir -p build
    cd    build

    meson setup --prefix=/usr     \
        --libdir=/usr/lib         \
        --buildtype=release       \
        --wrap-mode=nofallback ..
}

build() {
    set -e
    cd build
    ninja -j${IGOS_JOBS}
}

check() {
    set -e
    cd build
    ninja test
}

do_install() {
    set -e
    cd build
    DESTDIR="$DESTDIR" ninja install

    # Symlink machine-id for D-Bus compatibility
    mkdir -pv "${DESTDIR}/var/lib/dbus"
    ln -sfv /etc/machine-id "${DESTDIR}/var/lib/dbus"

    # Set setuid bit on dbus-daemon-launch-helper — required for the
    # system bus to spawn activated services on demand. Mode 4750 with
    # group restriction to messagebus per BLFS 13.0 dbus-1.16.2
    # canonical (only the dbus-daemon process running as messagebus
    # can invoke the helper). Must be set here because tar-based
    # deployment strips setuid bits during extraction (pkm restores
    # them from tarball metadata post-extract; see
    # pkm/installer.py:475-490). Ownership is set in post_install on
    # the live system because the PEP 706 data filter in the
    # deploy-extract path strips uid/gid.
    chmod 4750 "${DESTDIR}/usr/libexec/dbus-daemon-launch-helper"
}

post_install() {
    set -e
    # messagebus user/group is declared by upstream's
    # /usr/lib/sysusers.d/dbus.conf. Process it explicitly here so the
    # user exists before the subsequent chown resolves. Idempotent;
    # safe at both build VM time (live chroot's /etc/passwd) and
    # laptop install time (chroot-context; pkm canonical pre-hook may
    # have already run sysusers — re-run is a no-op).
    systemd-sysusers /usr/lib/sysusers.d/dbus.conf
    chown root:messagebus /usr/libexec/dbus-daemon-launch-helper
    # The chown above clears the setuid bit on a regular file (kernel behavior,
    # even for root) — so this hook has been shipping the launch helper INERT
    # on every install to date, leaving the system bus unable to spawn
    # activated services. Restore mode 4750 AFTER the chown. Package-local twin
    # of the L29 staging-chokepoint strip; mode per do_install / BLFS 13.0.
    chmod 4750 /usr/libexec/dbus-daemon-launch-helper
}
