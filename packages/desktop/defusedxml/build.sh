#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# defusedxml 0.7.1 — hardened XML parsing (entity-expansion / external-entity
# protection over the stdlib parsers). Not in BLFS — InterGenOS extra tier
# (icon-generation toolchain dep: cairosvg parses untrusted-shaped SVG XML
# through this). Pure-python sdist from PyPI; offline pip wheel+install
# (the python-requests pattern).

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
        --find-links dist --root="$DESTDIR" defusedxml
}
