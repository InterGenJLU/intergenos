#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# python-msgpack 1.2.1
# InterGenOS core package (BLFS-class addition, core-extra tail)
#
# MessagePack serializer for Python. Build-time dependency of the
# compute tier's rocblas: Tensile's fast logic-file path imports
# msgpack (the yaml fallback works but is far slower). Cython is in
# core, so the C extension builds; the pure-Python fallback would also
# satisfy Tensile.

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    pip3 wheel \
        --no-build-isolation \
        --no-deps \
        --no-cache-dir \
        -w dist \
        $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps \
        --no-index \
        --no-user \
        --no-deps \
        --no-cache-dir \
        --find-links dist \
        --root="$DESTDIR" \
        msgpack
}
