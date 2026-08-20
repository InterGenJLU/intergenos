#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# tcpdump 4.99.6 — captures packets from a network interface or a saved file
# and prints them in a readable form.
#
# Build facts verified against the pinned tarball:
#   - autotools with a generated ./configure (a CMakeLists.txt also exists;
#     autotools is the path upstream documents for release tarballs).
#   - The binary installs into $(bindir) — /usr/bin/tcpdump — and the manual
#     page is generated from tcpdump.1.in, so it lands in man1, not man8.
#     Several distributions place the binary in /usr/sbin; upstream's own
#     Makefile does not, and following upstream keeps the path the same as the
#     one every tcpdump document refers to.
#
# --with-crypto is left at its default (enabled, using OpenSSL) so that
# captured ESP traffic can be decrypted when the user supplies a key. Turning
# it off would silently remove that ability from a diagnostic tool.
#
# tcpdump drops privileges to an unprivileged user when started as root with
# -Z, which is the standard way to run it; nothing in this recipe changes that
# behaviour.

configure() {
    set -e
    ./configure               \
        --prefix=/usr         \
        --mandir=/usr/share/man
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
    # This tarball's Makefile has exactly one install rule (`install: all`,
    # Makefile.in:421) and no separate install-man target — the man page is
    # installed by `install` itself (tcpdump.1 into man1). Asking for the
    # nonexistent target stopped make after the payload was already staged.
    make DESTDIR="$DESTDIR" install
}
