#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Wheel 0.46.3
# LFS 13.0 Section 8.56
#
# DESTDIR exception: pip uses --root instead of DESTDIR.

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist wheel
}
