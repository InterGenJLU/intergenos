#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# CairoSVG 2.9.0 — SVG converter (the icon-generation toolchain's engine)
# Not in BLFS — InterGenOS extra tier. Authored 2026-07-22 so the icon
# toolchain runs pip-free from the mirror (the project's package/network-pull
# rule: mirror-first; these were previously pip-pulled per the
# generator's own run instructions). Pure-python setuptools sdist from
# PyPI (dist name "CairoSVG"; pip normalizes to cairosvg); offline pip
# wheel+install (the python-requests pattern).

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
        --find-links dist --root="$DESTDIR" cairosvg
}
