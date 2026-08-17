#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# util-linux-pass2 2.41.3 — pass 2 with libcap-ng-backed setpriv.
# Flags match packages/core/util-linux-core/build.sh (LFS 13.0 §8.82)
# EXACTLY except --disable-setpriv -> --enable-setpriv: the book
# disables setpriv because libcap-ng is not part of LFS; this pass
# builds in the core-extra wave where libcap-ng is present. The
# explicit enable is fail-closed — configure aborts if libcap-ng is
# missing instead of silently dropping the tool.

configure() {
    set -e
    ./configure --bindir=/usr/bin      \
        --libdir=/usr/lib              \
        --runstatedir=/run             \
        --sbindir=/usr/sbin            \
        --disable-chfn-chsh            \
        --disable-login                \
        --disable-nologin              \
        --disable-su                   \
        --enable-setpriv               \
        --disable-runuser              \
        --disable-pylibmount           \
        --disable-liblastlog2          \
        --disable-static               \
        --without-python               \
        ADJTIME_PATH=/var/lib/hwclock/adjtime \
        --docdir=/usr/share/doc/util-linux-2.41.3
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # LFS: touch /etc/fstab to prevent two test failures
    touch /etc/fstab

    # WARNING: Running tests as root can be harmful to the system
    # Some tests require specific kernel config options
    chown -R tester .
    su tester -c "make -k check" || true
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Set setuid bits — mount/umount need setuid for non-root user mounts.
    # Must be set here because tar-based deployment strips setuid bits.
    chmod 4755 "${DESTDIR}/usr/bin/mount"
    chmod 4755 "${DESTDIR}/usr/bin/umount"

    # Fail loud if the whole point of this pass is missing (Rule 21 —
    # never ship the pass-1 setpriv-less state under this name).
    test -x "${DESTDIR}/usr/bin/setpriv"
}
