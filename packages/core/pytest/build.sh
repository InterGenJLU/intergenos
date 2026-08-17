#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# pytest 9.1.1 — testing framework. The InterGen test harness runs on it, so it
# ships in the ISO. Offline PEP 517 wheel build (setuptools + setuptools-scm
# backend already in the chroot); runtime deps iniconfig/packaging/pluggy/
# pygments are all built earlier in the core phase.

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" pytest
}
