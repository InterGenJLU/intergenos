#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Gzip 1.14
# LFS 13.0 Section 8.67

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # AppArmor: zgrep confines /usr/bin/zgrep, which this package installs
    # (its attachment also names xzgrep, which the xz package installs). The profile
    # text is upstream's, staged by the apparmor package in
    # /usr/share/apparmor/extra-profiles, which nothing loads; this package links
    # it into /etc/apparmor.d because this package is what puts the program on
    # the machine, and the apparmor unit loads every file in /etc/apparmor.d at
    # boot. apparmor is a runtime dependency so the link cannot dangle. See
    # packages/core/apparmor/README.md, "Where a profile lives".
    install -dm755 "${DESTDIR}/etc/apparmor.d"
    for profile in zgrep; do
        ln -s "../../usr/share/apparmor/extra-profiles/${profile}" \
            "${DESTDIR}/etc/apparmor.d/${profile}"
    done
}
