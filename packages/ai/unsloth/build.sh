#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# unsloth 2026.7.4 — pure-python setuptools build; the heavy lifting lives in
# its runtime closure (pytorch-ROCm, triton, bitsandbytes, aotriton-backed
# SDPA). NEVER their curl|sh installer or prebuilt binaries — this package IS
# the from-source integration the Q2b mandate requires.

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
         --root="$DESTDIR" unsloth
}
