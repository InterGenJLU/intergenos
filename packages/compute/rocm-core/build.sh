#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocm-core 7.2.4 — ROCm platform base metadata
#
# Ships the platform-contract artifacts every AMD-packaged ROCm install
# carries and that consumers read directly: /opt/rocm/.info/version
# (rccl configure auto-detect class), include/rocm-core/rocm_version.h
# (rccl hip_rocm_version_info.h:42), and librocm-core (rocm_getpath).
# See package.yml for the class rationale.

configure() {
    set -e
    mkdir -p build
    # ROCM_VERSION drives the generated version header + .info/version —
    # keep in lockstep with version: in package.yml.
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DROCM_VERSION=7.2.4 \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build

    # Fail loudly if the platform-contract artifacts did not land where
    # declared (upstream installs `version` into `.info/` relative to
    # the prefix and the headers into include/rocm-core/).
    test -e "${DESTDIR}/opt/rocm/.info/version"
    test -e "${DESTDIR}/opt/rocm/include/rocm-core/rocm_version.h"
}
