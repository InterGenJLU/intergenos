#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rccl 7.2.4 — NCCL-compatible collectives for multi-GPU (2x R9700)
# Source: standalone ROCm/rccl repo at the rocm-7.2.4 tag
#
# BUILD_TESTS defaults OFF and stays OFF; BUILD_LOCAL_GPU_TARGET_ONLY
# stays OFF (targets are DECLARED — the chroot has no GPU). ROCTX binds
# against the installed roctracer (see package.yml — the silent-degrade
# guard). MSCCL kernels keep their upstream default ON (in-tree, no
# network).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # Upstream unquoted-variable bug: the MSCCL++ distro check
    # (CMakeLists.txt:374) expands ${HOST_OS_ID} bare, and CMake's if()
    # rejects the malformed token list when /etc/os-release has no ID=
    # line — even though ENABLE_MSCCLPP defaults OFF (if() parses all
    # arguments before evaluating). The patch quotes the expansions;
    # MSCCL++ itself is gfx942/gfx950-only and stays OFF.
    patch -p1 < "${IGOS_PACKAGE_DIR:-$BUILD_DIR}/patches/0001-quote-host-os-id-in-mscclpp-distro-check.patch"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    mkdir -p build
    # EXPLICIT_ROCM_VERSION: upstream's own knob (CMakeLists:190) — the
    # auto-detect path hard-reads /opt/rocm/.info/version, an AMD
    # rocm-core packaging artifact a from-source platform does not ship.
    # Keep in lockstep with version: in package.yml.
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DEXPLICIT_ROCM_VERSION=7.2.4 \
        -DBUILD_TESTS=OFF \
        -DBUILD_LOCAL_GPU_TARGET_ONLY=OFF \
        -DROCM_SYMLINK_LIBS=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5

    # Fail-closed on the silent-degrade path this recipe exists to close:
    # a missing roctx64 leaves ROCTX_LIB-NOTFOUND in the cache and upstream
    # merely WARNs (CMakeLists:388) — here it is fatal.
    if grep -q "ROCTX_LIB.*NOTFOUND" build/CMakeCache.txt; then
        echo "FATAL: ROCTX degraded despite the roctracer dep (roctx64 not found)" >&2
        exit 1
    fi
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
