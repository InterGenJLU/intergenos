#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocm-hip 7.2.4 — HIP runtime (CLR) + compiler driver (hipcc)
# Sources: projects/clr + projects/hip (rocm-systems) and amd/hipcc
#          (llvm-project fork — extracted explicitly, Rule 5)
#
# Two-stage build, per the pinned CLR CMake's own documented invocation
# (projects/clr/CMakeLists.txt header): hipcc first (a small C++ driver
# + the perl hipconfig), then CLR pointed at HIP_COMMON_DIR (the HIP API
# headers project) and HIPCC_BIN_DIR (the just-built hipcc).
#
# HIP_PLATFORM=amd only. HIPNV_DIR is DELIBERATELY not passed: validated
# against the pinned hipamd CMakeLists (~line 279) — it is FATAL-checked
# only under HIP_PLATFORM=nvidia and otherwise used solely to optionally
# install nvidia headers; the AMD build needs neither, so the hipother
# project stays entirely out of the recipe.
# CLR_BUILD_OCL=OFF: OpenCL is outside the ruled compute-set scope (the
# HIP path is what llama.cpp uses); this is the upstream-supported
# component toggle, not a feature mask.
# runtime deps carry rocm-llvm because hipcc IS a driver around
# amdclang++ — a HIP-variant engine rebuild on a target box compiles
# through this package's toolchain path.

configure() {
    set -e

    # Rule 5: explicit extraction of the secondary tarball's amd/hipcc
    # subtree only (the full fork tarball is 250 MB of source; hipcc is
    # the one directory this package consumes from it).
    tar -x -z -o -f "${IGOS_SOURCES}/rocm-llvm-project-7.2.4.tar.gz" \
        "llvm-project-rocm-7.2.4/amd/hipcc"

    # Stage 1: hipcc + hipconfig
    cmake -G Ninja \
        -S llvm-project-rocm-7.2.4/amd/hipcc \
        -B build-hipcc \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5

    cmake --build build-hipcc -j "${IGOS_JOBS}"

    # Stage 2: CLR (hipamd + rocclr) against the staged hipcc
    cmake -G Ninja \
        -S projects/clr \
        -B build-clr \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DHIP_COMMON_DIR="${PWD}/projects/hip" \
        -DHIPCC_BIN_DIR="${PWD}/build-hipcc" \
        -DHIP_CATCH_TEST=0 \
        -DCLR_BUILD_HIP=ON \
        -DCLR_BUILD_OCL=OFF \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cmake --build build-clr -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build-hipcc
    DESTDIR="$DESTDIR" cmake --install build-clr

    # Runtime loader path for the whole /opt/rocm stack. Nothing shipped an
    # ld.so.conf entry for /opt/rocm/lib, so every /opt/rocm binary failed
    # shared-library load on a stock system (live-witnessed: rocminfo could
    # not load libhsa-runtime64.so.1 when hipcc invoked it). rocm-hip owns
    # the snippet as the runtime hub every HIP consumer links through —
    # the same single-owner shape as the reference distro's rocm.conf.
    install -Dm644 /dev/stdin "${DESTDIR}/etc/ld.so.conf.d/rocm.conf" <<'CONF'
/opt/rocm/lib
CONF

    # Decided 2026-08-19, same shape and same reason as the rocminfo recipe's
    # /usr/bin symlinks: /opt/rocm/bin is on no default PATH, and consumers
    # exec these two by bare name. The tree carries its own proof that the
    # bare name does not resolve today — packages/ai/bitsandbytes/build.sh
    # has to prepend /opt/rocm/bin to PATH in both configure() and build()
    # because its upstream CMakeLists calls `hipconfig --version` by bare
    # name, and packages/compute/llama-cpp-hip/build.sh reaches hipconfig
    # through its absolute path for the same reason.
    #
    # A build-time PATH export fixes only the build. At runtime the same
    # bare-name call from a service subprocess still finds nothing, and that
    # failure is silent: the measured consequence for the sibling tool was a
    # GPU library falling back to a default warp size of 64 on wave32
    # silicon, mis-detecting on every machine. profile.d was rejected there
    # for the same reason it would be wrong here — it never reaches a
    # systemd service. /usr/bin symlinks fix every exec context uniformly.
    mkdir -p "${DESTDIR}/usr/bin"
    ln -sf /opt/rocm/bin/hipcc "${DESTDIR}/usr/bin/hipcc"
    ln -sf /opt/rocm/bin/hipconfig "${DESTDIR}/usr/bin/hipconfig"
}
