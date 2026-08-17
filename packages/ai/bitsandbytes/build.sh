#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# bitsandbytes 0.49.2 — k-bit quantization for PyTorch, ROCm/HIP backend.
# Multi-backend CMake build (-DCOMPUTE_BACKEND=hip) driven through the
# upstream scikit-build-core backend; compiles csrc/{ops,kernels}.hip into
# libbitsandbytes_rocm<hipver>.so against the in-tree ROCm math libraries
# (hipblas / hiprand / hipsparse), then installs the Python package.

configure() {
    set -e
    # HIP toolchain roots (same detection pins as the compute math libs).
    export ROCM_PATH=/opt/rocm
    export HIP_PATH=/opt/rocm
    export PATH="/opt/rocm/bin:$PATH"   # hipconfig --version (CMakeLists.txt:221)
    GPU_TARGETS="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}"
    export BNB_GPU_TARGETS="${GPU_TARGETS}"
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm
    export HIP_PATH=/opt/rocm
    export PATH="/opt/rocm/bin:$PATH"

    # scikit-build-core drives CMake; pass the HIP backend selection and the
    # RDNA target set as cmake defines via config-settings (the backend's
    # documented override path). --no-build-isolation so the in-tree build
    # deps are used as-is (matches the ai-tier Python-package idiom).
    # env -u DESTDIR: scikit-build-core's internal cmake-install honors an
    # exported DESTDIR and packs a slim wheel (the pybind11-proven redirect
    # class; preempted here before it fired). do_install passes DESTDIR
    # inline, unaffected.
    env -u DESTDIR pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir \
        --config-settings=cmake.define.COMPUTE_BACKEND=hip \
        --config-settings=cmake.define.BNB_ROCM_ARCH="${IGOS_GPU_TARGETS:?FATAL: gpu_targets not declared in package.yml/plumbing}" \
        --config-settings=cmake.define.CMAKE_HIP_COMPILER=/opt/rocm/lib/llvm/bin/amdclang++ \
        --config-settings=cmake.define.CMAKE_PREFIX_PATH=/opt/rocm \
        "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist \
        --no-cache-dir --no-user --root="$DESTDIR" bitsandbytes

    # Fail-loud gate: the whole point of this recipe is the HIP
    # shared object. If the ROCm compile silently produced no library, the
    # install is a hollow pure-Python shell that fails at first quantize on
    # target — fail LOUD here instead.
    SP="$DESTDIR/usr/lib/python3.14/site-packages/bitsandbytes"
    if ! ls "$SP"/libbitsandbytes_rocm*.so >/dev/null 2>&1; then
        echo "FATAL: no libbitsandbytes_rocm*.so installed under $SP — the HIP backend did not build" >&2
        exit 1
    fi
}
