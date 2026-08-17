#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# frozenlist 1.8.0 — List-like structure that can be made immutable (hashable)

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD  # C extension — requires setuptools + wheel
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" frozenlist
}
