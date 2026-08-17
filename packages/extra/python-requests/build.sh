#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# python-requests 2.34.2 — HTTP library
# Not in BLFS — InterGenOS extra tier (virtualization stack support)
#
# Hard runtime dependency of virt-manager's virtinst (install-tree /
# URL media fetching imports requests unconditionally on the VM-create
# path). TLS verification flows through python-certifi, which on
# InterGenOS serves the SYSTEM CA bundle. Pure-python sdist from PyPI;
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
    pip3 install --no-index --no-user --no-deps --no-cache-dir --ignore-installed \
        --find-links dist --root="$DESTDIR" requests
}
