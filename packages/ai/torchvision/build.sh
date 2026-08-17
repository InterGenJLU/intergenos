#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# torchvision 0.25.0 — builds its C++ image-codec extensions against the
# installed pytorch (build-order: pytorch first). Video/ffmpeg path OFF by
# default upstream (deprecated); image codecs (jpeg/png/webp) detect from the
# declared system libs — verify the detection lines in the build log at the
# proof build (a codec silently missing = declaration gap, fix the recipe).

configure() {
    set -e
    # setuptools 82 removed pkg_resources; the 0.25.0 sdist's setup.py still
    # imports it (build-time only). Fails LOUD (patch reject) if upstream
    # reworks setup.py. (Decided 2026-07-22.)
    BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    patch -Np1 -i "$BUILD_DIR/setuptools82-no-pkg-resources.patch"
}

build() {
    set -e
    export FORCE_CUDA=0
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" torchvision
}
