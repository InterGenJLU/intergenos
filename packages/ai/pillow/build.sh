#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# pillow 12.3.0 — C-native imaging (unsloth-zoo dep). Codec support detects
# from the declared system libs; jp2/openjpeg absence is a recorded gap
# (package.yml), not a silent one.

configure() {
    set -e
    :
}

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" pillow
}
