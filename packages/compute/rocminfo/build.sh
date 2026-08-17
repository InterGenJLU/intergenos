#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocminfo 7.2.4 — ROCm agent/system reporter
# Source: projects/rocminfo inside the rocm-systems monorepo
#
# ROCRTST_BLD_TYPE=Release is the project's own (nonstandard) build-type
# knob — also avoids a _FORTIFY_SOURCE redefinition error in its default
# debug shape. rocm_agent_enumerator is a python3 script at runtime;
# pciutils backs its lspci fallback path when KFD is not initialized.

configure() {
    set -e
    cd projects/rocminfo
    mkdir -p build

    cmake -S . -B build \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DROCRTST_BLD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cd projects/rocminfo
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocminfo
    DESTDIR="$DESTDIR" cmake --install build

    # Decided 2026-08-13: /opt/rocm/bin is on no default PATH, and callers
    # exec these tools by bare name from non-login contexts (service
    # subprocesses) where profile.d never applies — measured consequence:
    # a GPU-library subprocess call to rocminfo failed silently and the
    # warp size defaulted to 64 on wave32 silicon. /usr/bin symlinks fix
    # every exec context uniformly; a PATH edit would miss services.
    mkdir -p "${DESTDIR}/usr/bin"
    ln -sf /opt/rocm/bin/rocminfo "${DESTDIR}/usr/bin/rocminfo"
    ln -sf /opt/rocm/bin/rocm_agent_enumerator "${DESTDIR}/usr/bin/rocm_agent_enumerator"
}
