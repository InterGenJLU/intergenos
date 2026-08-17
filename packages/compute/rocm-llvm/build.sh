#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# rocm-llvm 7.2.4 — AMD's LLVM fork (amdclang/lld/compiler-rt)
# https://github.com/ROCm/llvm-project (tag rocm-7.2.4)
#
# The compute tier's GPU-kernel compiler. Installs to /opt/rocm/lib/llvm
# (the ROCm >=6 layout every downstream ROCm package expects), with the
# amdclang* driver symlinks in /opt/rocm/bin. Coexists with the system
# LLVM by construction — nothing lands in /usr.
#
# Flag set follows AMD's own production build recipe
# (ROCm/tools/rocm-build/build_lightning.sh, cross-referenced via the
# Arch rocm-llvm 7.2.4 packaging of the same tag), with two deliberate
# reductions for our scope, both compile-target removals rather than
# feature masks:
#   - LLVM_TARGETS_TO_BUILD drops NVPTX (no CUDA compilation on this
#     package's path; the CUDA variant work is a separate arc).
#   - clang-tools-extra is not built (clangd/clang-tidy are developer
#     tooling, not needed to compile the ROCm stack or HIP kernels).
# The runtimes bootstrap (compiler-rt -> libcxx/libcxxabi/libunwind,
# static, headers-not-installed) is kept exactly as AMD builds it — comgr
# and the HIP toolchain expect amdclang's own runtime layout.
#
# LLVM_LINK/BUILD_LLVM_DYLIB=OFF (static LLVM libs): rocm-comgr links the
# LLVM/Clang static archives into libamd_comgr.so — do not strip the .a
# set from the install.

configure() {
    set -e

    # Teach clang's GCC-installation detection this distro's vendor triple
    # (x86_64-igos-linux-gnu is not in upstream's hardcoded alias list, so
    # the built clang found neither libstdc++ headers nor the -lgcc member
    # of the libgcc_s.so linker script). patch fails LOUD if upstream
    # moves the list — no silent no-op.
    BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    patch -p1 < "$BUILD_DIR/gcc-detection-igos-triple.patch"

    mkdir -p build

    cmake -G Ninja -S llvm -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/rocm/lib/llvm \
        -DLLVM_HOST_TRIPLE=x86_64-pc-linux-gnu \
        -DLLVM_ENABLE_PROJECTS="clang;lld" \
        -DCLANG_ENABLE_AMDCLANG=ON \
        -DPACKAGE_VENDOR="AMD" \
        -DLLVM_ENABLE_RUNTIMES="compiler-rt;libunwind;libcxx;libcxxabi;openmp" \
        -DLIBCXX_ENABLE_SHARED=OFF \
        -DLIBCXX_ENABLE_STATIC=ON \
        -DLIBCXX_INSTALL_LIBRARY=OFF \
        -DLIBCXX_INSTALL_HEADERS=OFF \
        -DLIBCXXABI_ENABLE_SHARED=OFF \
        -DLIBCXXABI_ENABLE_STATIC=ON \
        -DLIBCXXABI_INSTALL_STATIC_LIBRARY=OFF \
        -DLLVM_TARGETS_TO_BUILD="AMDGPU;Native" \
        -DCLANG_DEFAULT_LINKER=lld \
        -DENABLE_LINKER_BUILD_ID=ON \
        -DCLANG_DEFAULT_RTLIB=compiler-rt \
        -DCOMPILER_RT_DEFAULT_TARGET_ONLY=ON \
        -DCLANG_DEFAULT_UNWINDLIB=libgcc \
        -DLLVM_INSTALL_UTILS=ON \
        -DLLVM_ENABLE_BINDINGS=OFF \
        -DLLVM_LINK_LLVM_DYLIB=OFF \
        -DLLVM_BUILD_LLVM_DYLIB=OFF \
        -DLLVM_ENABLE_OCAMLDOC=OFF \
        -DLLVM_INCLUDE_BENCHMARKS=OFF \
        -DLLVM_BUILD_TESTS=OFF \
        -DLLVM_INCLUDE_TESTS=OFF \
        -DCLANG_INCLUDE_TESTS=OFF \
        -DLLVM_ENABLE_ZLIB=ON \
        -DLLVM_ENABLE_ZSTD=ON \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5
}

build() {
    set -e
    # AMD's staged bootstrap (build_lightning.sh): the compiler + lld +
    # compiler-rt first, then the runtimes against the just-built clang,
    # then everything remaining. ninja -j is governed by the pool cmake
    # configured; cap with IGOS_JOBS explicitly for determinism.
    cmake --build build -j "${IGOS_JOBS}" -- clang lld compiler-rt
    cmake --build build -j "${IGOS_JOBS}" -- runtimes cxx
    cmake --build build -j "${IGOS_JOBS}"
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build

    # ROCm layout compatibility symlinks:
    # - /opt/rocm/llvm -> lib/llvm (pre-6.0 path some downstream CMake probes)
    # - amdclang* drivers surfaced in /opt/rocm/bin (downstream packages
    #   invoke them from there; matches AMD's shipped layout)
    mkdir -p "${DESTDIR}/opt/rocm/bin"
    ln -sv lib/llvm "${DESTDIR}/opt/rocm/llvm"
    local _c
    for _c in amdclang amdclang++ amdclang-cl amdclang-cpp amdlld; do
        ln -sv ../lib/llvm/bin/${_c} "${DESTDIR}/opt/rocm/bin/${_c}"
    done
}
