#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# poetry-core 2.4.1 — Poetry's PEP 517 build backend.
#
# Build dependency for the InterGen web-UI / console Python stack: pkgconfig,
# aiohappyeyeballs, and rich all declare build-backend = poetry.core.masonry.api.
# Under our offline --no-build-isolation builds, that backend must already be
# installed, so poetry-core is packaged here ahead of those consumers.
#
# Self-bootstrapping: poetry-core's own pyproject.toml uses backend-path=["src"]
# with requires=[] — it builds itself from its in-tree src/ with no external
# build deps, so --no-build-isolation works with nothing pre-installed.

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" poetry-core
}
