#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# logrotate 3.22.0 — rotation, compression and removal of system log files.
#
# WHY THIS PACKAGE EXISTS. The tree already installs
# /etc/logrotate.d/intergen-tool-dispatch (packages/ai/intergen/build.sh) and
# documents it in intergen(1) as "the canonical rotation mechanism on Linux",
# but no logrotate was ever packaged, so nothing consumed that snippet and the
# tool-dispatch audit log had no bound on its growth. This package supplies the
# consumer.
#
# Build system verified against the pinned tarball, not assumed:
#   - autotools with a pre-generated ./configure (autogen.sh present but not
#     needed for a release tarball).
#   - Makefile.am installs sbin_PROGRAMS = logrotate and
#     dist_man_MANS = logrotate.8 logrotate.conf.5 — so the man pages come from
#     upstream's own install rule and need no handling here.
#   - configure.ac gates ACL support on AC_CHECK_LIB([acl],[acl_get_file]) and
#     SELinux on AC_CHECK_LIB([selinux],...). ACL is enabled explicitly because
#     acl is in the tree; SELinux is left off because this distribution ships
#     AppArmor, so --without-selinux states that rather than letting configure
#     decide from what happens to be installed in the chroot.
#   - The configuration file, the drop-in directory contents and the systemd
#     units live under examples/ and are NOT installed by `make install`; they
#     are installed here explicitly.
#
# The service and timer are installed verbatim from upstream. Upstream's
# logrotate.service already carries the hardening set (ProtectSystem=full,
# PrivateDevices, ProtectKernelModules, MemoryDenyWriteExecute, …) and comments
# each protection it deliberately omits and why (no ProtectHome because user
# directories hold logs, no PrivateNetwork because rotation can mail, no
# NoNewPrivileges because third-party rotate scripts run under it). Re-authoring
# it would replace an audited upstream file with our own less-reviewed one.

configure() {
    set -e
    ./configure                                                \
        --prefix=/usr                                          \
        --sbindir=/usr/sbin                                    \
        --mandir=/usr/share/man                                \
        --with-acl                                             \
        --without-selinux                                      \
        --with-state-file-path=/var/lib/logrotate/logrotate.status
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}

post_install() {
    set -e

    # Main configuration: weekly rotation, four generations kept, dateext
    # suffixes, and the `include /etc/logrotate.d` that makes every package's
    # drop-in take effect.
    install -Dm644 examples/logrotate.conf "${DESTDIR}/etc/logrotate.conf"

    # The drop-in directory. intergen installs its snippet into this path.
    install -d -m 755 "${DESTDIR}/etc/logrotate.d"

    # Login-record rotation. No package owns /var/log/btmp or /var/log/wtmp,
    # which is why upstream ships these snippets. On this system
    # intergenos-base-files CREATES those two files through its tmpfiles.d
    # entry and these snippets ROTATE them — different paths, different halves
    # of the same concern, no file-ownership collision between the packages.
    install -Dm644 examples/btmp "${DESTDIR}/etc/logrotate.d/btmp"
    install -Dm644 examples/wtmp "${DESTDIR}/etc/logrotate.d/wtmp"

    # Upstream's oneshot service and its daily timer.
    install -Dm644 examples/logrotate.service \
        "${DESTDIR}/usr/lib/systemd/system/logrotate.service"
    install -Dm644 examples/logrotate.timer \
        "${DESTDIR}/usr/lib/systemd/system/logrotate.timer"

    # Home of the state file named by --with-state-file-path. Created here
    # because logrotate writes the file but does not create its directory.
    install -d -m 755 "${DESTDIR}/var/lib/logrotate"
}
