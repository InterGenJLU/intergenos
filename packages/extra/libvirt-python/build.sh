#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libvirt-python 12.5.0 — Python bindings for libvirt
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# C-extension bindings generated from libvirt's API descriptions at
# build time (the sdist's generator.py, driven by pkg-config against
# the installed libvirt). virt-manager's virtinst runs on these.
# Version tracks libvirt (12.5.0 pairs with libvirt 12.5.0). Offline
# pip wheel+install (the python-msgpack pattern).

configure() {
    set -e
    : # No configure step — setup.py drives the API codegen itself.
}

build() {
    set -e
    pip3 wheel --no-build-isolation --no-deps --no-cache-dir -w dist "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-index --no-user --no-deps --no-cache-dir \
        --find-links dist --root="$DESTDIR" libvirt-python
}
