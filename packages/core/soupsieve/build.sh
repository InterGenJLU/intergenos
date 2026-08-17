#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# soupsieve 2.9.1 — CSS selector library for Python.
#
# Pure Python, hatchling-built. beautifulsoup4 declares it as a required
# dependency (soupsieve>=1.6.1) and its bs4/css.py imports it, so the two are
# authored together: shipping beautifulsoup4 without this would leave its
# select() path broken at import time, which is a degraded package, not a
# smaller one. Same recipe shape as python-ply / typing-extensions.
#
# --ignore-installed on the install step is load-bearing, not decorative: a
# rebuild inside a chroot that already carries this package otherwise lets pip
# satisfy the requirement from the environment, leaving DESTDIR — and the
# sealed archive — EMPTY while reporting success (the DFB-01/02 class,
# 2026-07-19).

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
        --no-cache-dir \
        --find-links dist \
        --root="$DESTDIR" \
        soupsieve
}
