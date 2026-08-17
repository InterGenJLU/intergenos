#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# winetricks 20260125 — Wine-prefix helper (a single POSIX-sh script; no compilation).
#
# The upstream Makefile installs the script, man page, bash-completion, .desktop,
# AppStream metainfo and icon under PREFIX, honoring DESTDIR (all four major distros
# install via 'make install' — no manual copying). There is nothing to configure or
# build; check() is a smoke test (the script runs and reports its date-version)
# because upstream's 'make check' (shellcheck/bashate lint + DLL-install verbs) needs
# lint tools and wine + network, none of which exist in-chroot.

configure() { :; }

build() { :; }

check() {
    set -e
    # Deterministic smoke: the shipped POSIX-sh script parses, runs, and self-reports
    # an 8-digit date-version. No wine, no network — so failure_policy is strict.
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        sh -c 'src/winetricks --version | grep -Eq "^[0-9]{8}"'
}

do_install() {
    set -e
    make PREFIX=/usr DESTDIR="$DESTDIR" install
    test -x "${DESTDIR}/usr/bin/winetricks"
}
