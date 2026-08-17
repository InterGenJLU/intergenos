#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# pyproject-metadata 0.11.0 — PEP 621 metadata class with core metadata generation
# BLFS 13.0

configure() {
    set -e
    :
}

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" --no-deps pyproject-metadata
}
