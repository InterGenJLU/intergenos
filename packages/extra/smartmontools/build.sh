#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# smartmontools 7.5 — S.M.A.R.T. disk health (smartctl + smartd). Standard GNU
# autotools build. Not in BLFS 13.0.
#
# Deliberate flag choices (Prime Directive / no silent network, deterministic paths):
#   --without-update-smart-drivedb : drops the GnuPG-verified live drive-database
#       updater script; we ship the tarball's drivedb.h and never fetch at runtime
#       (offline posture, and it keeps verify_paths deterministic — no conditional
#       binary or man page).
#   --with-systemdsystemunitdir=no : no smartd monitoring unit installed by default;
#       the D4 directive is post-install DIAGNOSTIC use (smartctl). Enabling the
#       monitoring daemon is a separate, opt-in decision.
#   --with-libsystemd=no / --with-libcap-ng=no / --with-selinux=no : optional
#       hardening libs pinned OFF explicitly rather than "auto" (no silent
#       degradation; smartctl's diagnostic function needs none of them).

configure() {
    set -e
    ./configure --prefix=/usr \
                --sbindir=/usr/sbin \
                --sysconfdir=/etc \
                --with-drivedbdir=/usr/share/smartmontools \
                --without-update-smart-drivedb \
                --with-systemdsystemunitdir=no \
                --with-libsystemd=no \
                --with-libcap-ng=no \
                --with-selinux=no
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install
}
