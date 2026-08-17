#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# unsloth-zoo 2026.7.4 — pure-python setuptools build (unsloth's utility/
# kernel companion; LGPL-3.0 — license verified per the Q2b first-gate).

configure() {
    set -e
    :
}

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" unsloth-zoo
}
