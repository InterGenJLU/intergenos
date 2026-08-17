#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# newt 0.52.25 — Text mode windowing toolkit
# BLFS 13.0

configure() {
    set -e
    # BLFS: disable static library installation
    sed -e '/install -m 644 $(LIBNEWT)/ s/^/#/' \
        -e '/$(LIBNEWT):/,/rv/ s/^/#/'          \
        -e 's/$(LIBNEWT)/$(LIBNEWTSH)/g'        \
        -i Makefile.in

    ./configure --prefix=/usr \
                --without-gpm-support
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
