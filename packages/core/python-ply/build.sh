#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenOS
#
# python-ply 3.11
# InterGenOS core package (BLFS-class addition, core-extra tail)
#
# Python Lex-Yacc. Pure-Python parsing tools; the lexer backend
# CppHeaderParser imports at runtime (its only dependency). Final
# upstream release — ply is complete software, not abandoned. Same
# recipe shape as python-joblib/python-msgpack.

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
        ply
}
