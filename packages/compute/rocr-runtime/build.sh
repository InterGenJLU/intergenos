#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocr-runtime 7.2.4 — HSA runtime (the ROCm userspace foundation)
# Source: projects/rocr-runtime inside the rocm-systems monorepo
#
# BUILD_SHARED_LIBS=ON is the supported production shape (the static
# path exists for embedded uses only). The runtime compiles its blit/trap
# GPU kernels at build time with the rocm-llvm clang + device-libs
# (hence both in build deps; xxd — shipped by vim's standard install —
# embeds the compiled kernels as headers, which is why vim appears as a
# build dep: it is the tree's xxd provider).
#
# rocprofiler-register is DELIBERATELY not built/linked: the pinned
# source treats it as optional by design (plain find_package + graceful
# else-branch, hsa-runtime CMakeLists ~line 355) and the profiler layer
# is explicitly outside the ruled compute-set scope. Absence means the
# runtime lacks the profiler-registration hook — inference is unaffected.
# If the profiler stack is ever wanted, rocprofiler-register is a sibling
# project in this same pinned monorepo (no new source pin needed).
#
# -DNDEBUG on CXXFLAGS silences optional-library warnings
# (ROCR-Runtime#89) — warning noise only, no behavior change.

configure() {
    set -e
    cd projects/rocr-runtime
    mkdir -p build

    cmake -G Ninja -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm \
        -DCMAKE_PREFIX_PATH=/opt/rocm \
        -DCMAKE_CXX_FLAGS="-DNDEBUG" \
        -DBUILD_SHARED_LIBS=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    cd projects/rocr-runtime
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    cd projects/rocr-runtime
    DESTDIR="$DESTDIR" cmake --install build
}
