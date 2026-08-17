#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# timm 1.0.28 — PyTorch Image Models. Pure Python, no compiled extension, so
# this follows the same wheel-build shape as the other Python libraries in this
# tier (diffusers, peft, transformers) rather than torchvision's, which has C++
# image-codec extensions to build.

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist \
        --no-cache-dir --no-user --root="$DESTDIR" timm
}
