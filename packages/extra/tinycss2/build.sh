#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# tinycss2 1.5.1 — low-level CSS parser
# Not in BLFS — InterGenOS extra tier (icon-generation toolchain dep:
# cairosvg/cssselect2 runtime requirement). Pure-python flit_core sdist
# from PyPI; offline pip wheel+install (the python-requests pattern).

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    pip3 wheel --no-build-isolation --no-deps --no-cache-dir -w dist "$PWD"
}

do_install() {
    set -e
    pip3 install --no-index --no-user --no-deps --no-cache-dir --ignore-installed \
        --find-links dist --root="$DESTDIR" tinycss2
}
