#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# cabextract 1.11 — Microsoft Cabinet (.cab) extractor (winetricks dependency).
# Standard autotools; the tarball bundles libmspack, so the default build is
# standalone with no external dependency (no system libmspack package in-tree).

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
    # Self-contained offline test suite (extracts bundled .cab fixtures); no
    # display, network, or root needed. strict per package.yml.
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
