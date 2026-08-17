#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# jq 1.8.2 — command-line JSON processor.
# Links the system oniguruma (tier:core) for regex support via --with-oniguruma
# rather than building the bundled submodule copy. --disable-maintainer-mode
# keeps the shipped autotools build from regenerating on tarball-timestamp skew.

configure() {
    set -e
    ./configure --prefix=/usr \
                --with-oniguruma=/usr \
                --disable-maintainer-mode
}

build() {
    set -e
    make -j"${IGOS_JOBS:-$(nproc)}"
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install
}
