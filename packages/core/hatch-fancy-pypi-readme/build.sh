#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# hatch-fancy-pypi-readme 25.1.0 — Hatch plugin for fancy PyPI READMEs
# BLFS 13.0

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-index --no-deps --find-links dist --no-user --root="$DESTDIR" hatch_fancy_pypi_readme
}
