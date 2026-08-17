#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# pkgconfig 1.6.0 — Python wrapper around the pkg-config CLI.
#
# Build dependency for aiohttp: aiohttp's [build-system].requires lists
# "pkgconfig" (it probes for system llhttp at build time). Under our offline
# --no-build-isolation builds the backend requires must be pre-installed, so
# pkgconfig is packaged here ahead of aiohttp.
#
# Pure-Python; build-backend = poetry.core.masonry.api, so poetry-core must be
# built+installed first (it is, immediately above in the core-extra order).

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" pkgconfig
}
