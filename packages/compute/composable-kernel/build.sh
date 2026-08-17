#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# composable-kernel 7.2.4 — CK operator templates + device op libraries
# Source: projects/composablekernel inside rocm-libraries
#
# ENABLE_CLANG_CPP_CHECKS=OFF: that switch arms clang-tidy/cppcheck
# lint passes (dev tooling, not product; cppcheck is not in the chroot).
# Tests/examples off. MIOPEN_REQ_LIBS_ONLY stays OFF (full library set —
# see package.yml).

configure() {
    set -e
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"

    # ROCM_PATH: HIP-root detection pin (same class as rocblas/rocwmma).
    export ROCM_PATH=/opt/rocm

    cd projects/composablekernel
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DCMAKE_CXX_FLAGS="-fcf-protection=none" \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DHIP_PLATFORM=amd \
        -DENABLE_CLANG_CPP_CHECKS=OFF \
        -DMIOPEN_REQ_LIBS_ONLY=OFF \
        -DBUILD_TESTING=OFF \
        -DGPU_TARGETS="${GPU_TARGETS}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm   # see configure(): HIP-root detection pin
    cd projects/composablekernel

    # Memory-bound jobs cap, PARALLELIZATION-DETECTED (decided 2026-07-18).
    # History: the instance-library template compiles peak ~2-4 GB RSS per
    # clang; -j16 on a 31 GB / 8 GB-swap build host drove a kernel
    # global_oom at ninja step ~30/3107 (clang-22 OOM-killed, unit Result:
    # oom-kill). The 2026-07-17 fix capped at a fixed -j8 — proven safe
    # there, but environment-blind: a 96 GB / 24-core host then idles
    # two-thirds of itself for a ~16 h package. The binding constraint is
    # MEMORY, not cores, so derive the cap from the builder's actual
    # MemAvailable at 4 GB per job (the measured worst-case clang peak),
    # bounded by IGOS_JOBS/nproc. A host that cannot report MemAvailable
    # falls back to the proven -j8 — never unbounded. Same
    # serialize-for-the-environment class as build-rules §2.10, applied as
    # a detected cap rather than a fixed one.
    local ck_jobs="${IGOS_JOBS:-8}"
    local avail_kb avail_jobs
    avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null)
    if [ -n "${avail_kb:-}" ] && [ "$avail_kb" -gt 0 ] 2>/dev/null; then
        avail_jobs=$(( avail_kb / 4194304 ))   # 4 GB per job
        [ "$avail_jobs" -lt 2 ] && avail_jobs=2
        [ "$ck_jobs" -gt "$avail_jobs" ] && ck_jobs="$avail_jobs"
        echo "[ck] parallelization detection: MemAvailable ${avail_kb} kB -> memory-safe jobs ${avail_jobs}, using -j${ck_jobs}"
    else
        [ "$ck_jobs" -gt 8 ] && ck_jobs=8
        echo "[ck] parallelization detection: MemAvailable unreadable -> proven fallback -j${ck_jobs}"
    fi

    cmake --build build -j "$ck_jobs"
}

do_install() {
    set -e
    cd projects/composablekernel
    DESTDIR="$DESTDIR" cmake --install build
}
