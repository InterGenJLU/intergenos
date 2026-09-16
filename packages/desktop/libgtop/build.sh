#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libgtop 2.41.3 — System monitoring library
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Decided 2026-09-16: the system-information helper ships unprivileged.
    # Upstream installs /usr/libexec/libgtop_server2 setuid root so that the
    # library can read process data through a privileged helper on systems where
    # a plain read is not enough. On this system it is never used: the library
    # reads the kernel's process filesystem in-process, and the helper is not
    # executed at all on the path its consumers take. Measured before this change
    # with the bit dropped: the library returned identical process, memory, cpu
    # and per-process data for every process on the machine, including
    # root-owned ones, and the desktop's system monitor ran and read the same
    # 105 process trees without ever executing the helper.
    #
    # Fail-closed: assert the bit is gone rather than trusting the chmod, so an
    # upstream install-mode change surfaces as a build failure.
    chmod 0755 "${DESTDIR}/usr/libexec/libgtop_server2"
    if [ -u "${DESTDIR}/usr/libexec/libgtop_server2" ] || \
       [ -g "${DESTDIR}/usr/libexec/libgtop_server2" ]; then
        echo "libgtop: /usr/libexec/libgtop_server2 still carries a setuid or setgid bit — halting" >&2
        exit 1
    fi
}
