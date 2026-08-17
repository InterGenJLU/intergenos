#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# versioneer 0.29 — Easy VCS-based management of project version strings
# Single top-level module (py_modules=["versioneer"]); versioneer.py is assembled
# from the src/ fragments by setup.py's generate_versioneer_py() at build time.

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist \
        --no-cache-dir --no-user --root="$DESTDIR" versioneer
}
