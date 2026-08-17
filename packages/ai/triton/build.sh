#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# triton 3.6.0 — GPU kernel compiler, AMD/ROCm backend.
#
# held-pending-triton-llvm-proof: the LLVM decision item (package.yml) is
# resolved — LLVM_SYSPATH points at compute/triton-llvm (pristine LLVM 22.0.0
# at triton's pinned commit, MLIR+LLD, private prefix /opt/triton-llvm).
# Offline, system json/pybind11. Burn scheduling stays held until the
# triton + triton-llvm pair has been proven to build+link together on the
# validation workstation.

configure() {
    set -e
    export ROCM_PATH=/opt/rocm
    export PATH="/opt/rocm/bin:$PATH"

    # Offline: skip triton's LLVM download and its NVIDIA ptxas/cuobjdump
    # fetches, and disable the GoogleTest-fetching unit-test build
    # (setup.py honors TRITON_OFFLINE_BUILD).
    export TRITON_OFFLINE_BUILD=1

    # Build the AMD codegen backend only — the NVIDIA codegen backend would
    # require shipping proprietary executables (ptxas et al.) and stays out.
    export TRITON_CODEGEN_BACKENDS=amd

    # proton profiler header staging (the package.yml vendor exception):
    # merge the three pinned NVIDIA redistributable include/ trees into one
    # local dir. Compile-time inputs only — nothing from these archives is
    # installed or shipped; the proton shims dlopen at runtime.
    rm -rf .nvidia-include && mkdir .nvidia-include
    for a in cuda_cudart-linux-x86_64-12.8.57-archive \
             cuda_cupti-linux-x86_64-12.8.90-archive \
             cuda_nvcc-linux-x86_64-12.8.61-archive; do
        tar xf "${IGOS_SOURCES}/${a}.tar.xz" "${a}/include"
        cp -a "${a}/include/." .nvidia-include/
        rm -rf "${a}"
    done
    [ -f .nvidia-include/cuda.h ] || { echo "FATAL: merged NVIDIA header dir lacks cuda.h"; exit 1; }
    [ -f .nvidia-include/cupti.h ] || { echo "FATAL: merged NVIDIA header dir lacks cupti.h"; exit 1; }

    # System dependencies in place of triton's downloads (Rule 5 — no silent
    # network vendoring):
    #   LLVM_SYSPATH — compute/triton-llvm (pristine LLVM 22.0.0 @ triton's
    #   pinned commit f6ded0be, MLIR+LLD, private prefix).
    export LLVM_SYSPATH=/opt/triton-llvm
    #   JSON_SYSPATH — in-tree nlohmann-json (has include/nlohmann/json.hpp).
    export JSON_SYSPATH=/usr
    #   AMD backend headers: proton's shims include roctracer/, hip/ and hsa/
    #   headers by relative path, so this must be the include ROOT (the
    #   narrower include/roctracer subdir broke all three — burn-proven
    #   2026-07-22).
    export TRITON_ROCTRACER_INCLUDE_PATH=/opt/rocm/include
    # pybind11 is resolved via the in-tree pybind11 Python module
    # (setup.py: pybind11.get_include()); no SYSPATH needed.
}

build() {
    set -e
    export ROCM_PATH=/opt/rocm
    export PATH="/opt/rocm/bin:$PATH"
    export TRITON_OFFLINE_BUILD=1
    export TRITON_CODEGEN_BACKENDS=amd
    export LLVM_SYSPATH=/opt/triton-llvm
    export JSON_SYSPATH=/usr
    export TRITON_ROCTRACER_INCLUDE_PATH=/opt/rocm/include
    # proton CUDA/CUPTI shim headers — the merged dir configure() staged.
    export TRITON_CUPTI_INCLUDE_PATH="$PWD/.nvidia-include"
    [ -f "${TRITON_CUPTI_INCLUDE_PATH}/cupti.h" ] || { echo "FATAL: staged NVIDIA header dir missing — rerun configure"; exit 1; }

    # env -u DESTDIR: the cmake-inside-setuptools build is exposed to the
    # DESTDIR-redirect class (nested cmake install steps honor an inherited
    # DESTDIR); do_install passes DESTDIR inline and is unaffected.
    env -u DESTDIR pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist \
        --no-cache-dir --no-user --root="$DESTDIR" triton
}
