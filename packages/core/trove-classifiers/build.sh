#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# trove-classifiers 2026.1.14.14 — Canonical trove classifiers
# BLFS 13.0

configure() {
    set -e
    # BLFS: fix version string in setup.py
    sed -i '/calver/s/^/#/;$iversion="'${PKG_VERSION}'"' setup.py
}

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-index --no-deps --find-links dist --no-user --root="$DESTDIR" trove_classifiers
}
