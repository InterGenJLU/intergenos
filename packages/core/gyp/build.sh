#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# gyp 0.22.2 (gyp-next) — meta-build system, host tool for the lib32-nss
# build.sh gyp path. Standard pip wheel pattern (the jinja2 shape).
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
    pip3 install --ignore-installed --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist gyp-next
}
