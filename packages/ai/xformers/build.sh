#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# xformers 0.0.35 — attention building blocks (unsloth hard-dep on
# linux/x86_64). Built WITHOUT CUDA extensions (no CUDA on this stack);
# the python/torch-SDPA paths are the payload — see the package.yml
# execution-verify block for the ROCm acceptance bar.

configure() {
    set -e
    :
}

build() {
    set -e
    export XFORMERS_DISABLE_FLASH_ATTN=1
    export FORCE_CUDA=0
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" xformers
}
