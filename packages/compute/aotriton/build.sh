#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# aotriton 0.11.1b — AOT-compiled Triton attention kernels for ROCm SDPA.
#
# LLVM PATH B (see package.yml header): the bundled triton fork
# (third_party/triton @ f75e44a) is built FROM SOURCE against its own dedicated
# compute/aotriton-triton-llvm at /opt/aotriton-llvm (LLVM 21 @ 570885128, the
# fork's exact llvm-hash), via LLVM_SYSPATH + TRITON_OFFLINE_BUILD. Path A
# (reusing /opt/triton-llvm = LLVM 22) is retired: a full-major LLVM gap on
# triton's non-stable MLIR/LLVM APIs is ruled incompatible a priori (CUT-006).
#
# OFFLINE VENV (decision 3): aotriton's cmake spins up a Python venv and
# pip-installs requirements.txt + the bundled triton. We consume the chroot's
# installed set with NO network: a one-line tree-side patch makes the venv
# --system-site-packages (venv-system-site.patch — the only step env/flags
# cannot steer), and PIP_NO_INDEX / PIP_NO_BUILD_ISOLATION neuter every pip
# network path so already-installed requirements resolve as no-ops and the
# triton build uses the system build backend.

_aotriton_env() {
    export ROCM_PATH=/opt/rocm
    export HIP_PATH=/opt/rocm
    export PATH="/opt/rocm/bin:$PATH"
    # Bundled-triton offline build against its dedicated LLVM (path B).
    export TRITON_OFFLINE_BUILD=1
    export TRITON_CODEGEN_BACKENDS=amd
    export LLVM_SYSPATH=/opt/aotriton-llvm
    export JSON_SYSPATH=/usr
    export TRITON_ROCTRACER_INCLUDE_PATH=/opt/rocm/include/roctracer
    # Offline pip: consume the chroot's installed set, no index, no isolation.
    export PIP_NO_INDEX=1
    export PIP_NO_BUILD_ISOLATION=1
    # Upstream identifies the build via `git rev-parse` and a commit archive
    # has no .git; CMakeLists reads ENV{AOTRITON_CI_SUPPLIED_SHA1} (an env
    # var, NOT a -D cache define — verified in-source). Value = the recipe's
    # own source pin, the same commit pytorch pins as __AOTRITON_CI_COMMIT.
    export AOTRITON_CI_SUPPLIED_SHA1=d34f3b6c824df77d5c5788a2e7555b2398be4b79
}

configure() {
    set -e
    _aotriton_env

    # Rule 5: stage the submodules we need into their (empty) third_party slots
    # — commit archives carry no submodules. Only two of 0.11.1b's four are
    # needed for this build (see package.yml SUBMODULE CLOSURE NOTE).
    rm -rf third_party/triton third_party/incbin
    mkdir -p third_party/triton third_party/incbin
    tar -xzf "${IGOS_SOURCES}/aotriton-triton-f75e44a.tar.gz" --strip-components=1 -C third_party/triton
    tar -xzf "${IGOS_SOURCES}/aotriton-incbin-6e576cae.tar.gz" --strip-components=1 -C third_party/incbin
    [ -f third_party/triton/cmake/llvm-hash.txt ] || {
        echo "FATAL: aotriton-hyperjump triton fork not staged into third_party/triton" >&2
        exit 1
    }
    [ -f third_party/incbin/incbin.h ] || {
        echo "FATAL: incbin not staged into third_party/incbin" >&2
        exit 1
    }

    # Offline-venv lever — fails LOUD if upstream moved the venv-creation line.
    BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    patch -p1 < "$BUILD_DIR/venv-system-site.patch"
    # Explicit --no-build-isolation --no-index on the bundled-triton pip
    # line: the ninja-time custom command did not honor the PIP_* env
    # (burn-proven), so the offline posture rides argv flags.
    patch -p1 < "$BUILD_DIR/pip-offline-flags.patch"

    # cmake configure creates the venv and runs the requirements pip step at
    # THIS point (execute_process), so the offline env above must already be set
    # (including AOTRITON_CI_SUPPLIED_SHA1 — env-read, see _aotriton_env).
    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/aotriton \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DCMAKE_CXX_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        -DHIP_PLATFORM=amd \
        -DAOTRITON_TARGET_ARCH="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}" \
        -DAOTRITON_NOIMAGE_MODE=OFF \
        -DAOTRITON_NO_PYTHON=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    _aotriton_env
    # The bundled triton (pip install .) builds here as a custom_command target.
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
