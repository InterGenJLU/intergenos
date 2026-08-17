#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# torchao 0.16.0 — PyTorch-native quantization (unsloth-zoo dep). The CUDA
# kernel extensions do not apply on this stack; the python/CPU + torch-native
# paths are the payload. Builds against installed pytorch.

configure() {
    set -e
    :
}

build() {
    set -e
    export USE_CPP=0
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" torchao
}
