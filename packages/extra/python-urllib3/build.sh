#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# python-urllib3 2.7.0 — HTTP client library
# Not in BLFS — InterGenOS extra tier (virtualization stack support)
#
# Runtime dependency of python-requests (virt-manager install-media
# fetching). Pure-python sdist from PyPI (hatchling backend); offline
# pip wheel+install (the python-msgpack pattern).

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
        --find-links dist --root="$DESTDIR" urllib3
}
