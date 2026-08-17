#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenOS
#
# python-cppheaderparser 2.7.4
# InterGenOS core package (BLFS-class addition, core-extra tail)
#
# Pure-Python C++ header parser. Build-time dependency of the compute
# tier's rocm-hip: hipamd's code generation imports CppHeaderParser at
# configure (hipamd/src/CMakeLists.txt hard-requires it). Runtime dep:
# python-ply (the embedded lex/yacc backend). Same recipe shape as
# python-joblib/python-msgpack (the proven core-extra tail pattern).

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
        CppHeaderParser
}
