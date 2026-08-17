#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# numactl 2.0.19 — NUMA policy control library (libnuma) and utilities
#
# libnuma + NUMA policy tools. Runtime dependency of the compute tier's
# rocr-runtime (the HSA runtime binds process/memory placement through
# libnuma); useful on any multi-node box in its own right.

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
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
