#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2026 InterGenJLU
#
# pybind11 3.0.4 — header-only C++/Python binding library

configure() {
    set -e
    mkdir -p build
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr \
        -DPYBIND11_TEST=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
    # Also ship the python module: triton's setup.py (aotriton's bundled
    # fork) does `import pybind11` for its include paths — the cmake
    # install above ships headers/cmake only, satisfying the dependency
    # NAME but not the module. Offline pip against the source tree
    # (backend = scikit-build-core, declared in build deps); the wheel's
    # own vendored headers live inside site-packages/pybind11 and do not
    # collide with the /usr/include copy.
    # env -u DESTDIR: scikit-build-core's internal `cmake --install` honors
    # the exported DESTDIR and silently redirects the wheel-staging content
    # into ${DESTDIR}/tmp/... — the wheel then packs WITHOUT its include/
    # share payload (empirically proven: 0 vs 65 wheel data entries).
    # pip's --root (expanded before env runs) is the one staging mechanism.
    env -u DESTDIR python3 -m pip install --no-build-isolation --no-index \
        --root="$DESTDIR" --prefix=/usr .
}
