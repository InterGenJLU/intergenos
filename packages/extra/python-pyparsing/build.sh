#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# python-pyparsing 3.3.2 — parsing combinator library
# Not in BLFS — InterGenOS extra tier (virtualization stack support)
#
# Build-time dependency of spice-gtk (spice-common protocol codegen
# imports pyparsing). Pure-python sdist from PyPI (flit-core backend);
# offline pip wheel+install (the python-msgpack pattern).

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
    pip3 install --ignore-installed --no-index --no-user --no-deps --no-cache-dir \
        --find-links dist --root="$DESTDIR" pyparsing
}
