#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hatchling 1.28.0 — Python build backend
# BLFS 13.0

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-index --find-links dist --no-deps --no-cache-dir --no-user --root="$DESTDIR" hatchling
}
