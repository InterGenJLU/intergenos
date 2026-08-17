#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# semantic-version 2.10.0 — semantic-versioning library
# (setuptools-rust runtime dependency; F12 wave 2026-07-21).
# Offline PEP 517 wheel build (setuptools backend already in the chroot).

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" semantic-version
}
