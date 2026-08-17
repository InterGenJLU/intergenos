#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# beautifulsoup4 4.15.0 — HTML/XML parsing library for Python.
#
# Pure Python, hatchling-built. Consumed by scripts/parse-blfs-book.py, which
# turns the locally held BLFS book into build/blfs-packages.db; that database
# is what preflight-audit-coverage.py and preflight-silent-loss.py read, so
# the parser's dependency belongs on our own mirror rather than being pulled
# from the internet at the moment someone needs to regenerate the database.
#
# Upstream declares soupsieve>=1.6.1 and typing-extensions>=4.0.0 as required
# dependencies; both are packaged and declared as runtime deps rather than
# trimmed away. --ignore-installed on the install step is load-bearing: a
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
        beautifulsoup4
}
