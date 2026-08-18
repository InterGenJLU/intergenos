#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# wireguard-tools — wg and wg-quick, the user-space configuration tools for the
# in-kernel WireGuard tunnel.
#
# Build system verified against the pinned tarball: a hand-written Makefile
# under src/, no configure. Its install rule (src/Makefile:93-105) is explicit
# about what each switch produces:
#   - wg and man/wg.8 always;
#   - wg-quick plus its man page and /etc/wireguard only when WITH_WGQUICK=yes;
#   - the systemd template units only when WITH_WGQUICK and WITH_SYSTEMDUNITS
#     are both yes;
#   - bash completions when WITH_BASHCOMPLETION=yes.
# All three switches are set explicitly below. Left to their defaults they are
# decided by probing the BUILD environment — WITH_WGQUICK, for instance, is
# auto-enabled only if $(BINDIR)/bash exists at build time — which would make
# the contents of the package depend on the state of the chroot rather than on
# what this recipe says it ships.
#
# The version is upstream's own date-stamped scheme, taken verbatim from the
# snapshot name; the two independent sources agree on it (the project's git
# refs listing and its GitHub mirror both name v1.0.20260223 as the newest).

build() {
    set -e
    make -C src -j${IGOS_JOBS}                 \
        WITH_WGQUICK=yes                       \
        WITH_SYSTEMDUNITS=yes                  \
        WITH_BASHCOMPLETION=yes
}

do_install() {
    set -e
    make -C src install                        \
        DESTDIR="$DESTDIR"                     \
        PREFIX=/usr                            \
        SYSCONFDIR=/etc                        \
        SYSTEMDUNITDIR=/usr/lib/systemd/system \
        WITH_WGQUICK=yes                       \
        WITH_SYSTEMDUNITS=yes                  \
        WITH_BASHCOMPLETION=yes
}
