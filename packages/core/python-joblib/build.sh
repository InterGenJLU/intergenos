#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# python-joblib 1.5.3
# InterGenOS core package (BLFS-class addition, core-extra tail)
#
# Pure-Python pipelining/parallelism library. Build-time dependency of
# the compute tier's rocblas: Tensile declares joblib in its
# requirements.txt (kernel-generation parallelism), and the Tensile
# build venv resolves it from the system site-packages. Same recipe
# shape as python-msgpack (the proven core-extra tail pattern).

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
        joblib
}
