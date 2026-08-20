#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# julia 1.12.6 — The Julia programming language
# Not in BLFS — InterGenOS extra-tier language toolchain (RC001 unlock lane).
#
# HEAVY-BOOTSTRAP / LLVM-FORK VENDORING (the flagged hazard). Julia does NOT use
# a stock LLVM: it links a PATCHED LLVM fork (Julia carries codegen/GC patches
# atop a pinned LLVM major), so the in-tree llvm (21.1.8) cannot be used as a
# system LLVM — USE_SYSTEM_LLVM would fail. The source-pure, offline path is to
# build Julia's bundled LLVM fork (and the other bundled deps) FROM SOURCE:
#
#   * The pinned source is the `-full` release tarball, which bundles every dep
#     SOURCE tarball under deps/srccache/ (the light GitHub source would fetch
#     them over the network at build time — disallowed in the offline chroot).
#   * USE_BINARYBUILDER=0 makes the build compile those bundled deps from source
#     rather than downloading prebuilt BinaryBuilder artifacts. This includes the
#     LLVM fork, OpenBLAS, SuiteSparse, libuv, etc.
#
# BUILD-VERIFY items (the chroot build-verify leg — the heavy-bootstrap hazards):
#   * building the LLVM fork from source is the dominant cost (hours + high RAM);
#     schedule it accordingly.
#   * OpenBLAS builds from source and needs gfortran (present: gcc-core r4).
#   * JULIA_CPU_TARGET below keeps the shipped sysimage portable across target machines;
#     tune if a single-target build is wanted. If a bundled-dep build wall is hit,
#     STOP and document per Rule A (no third tactical patch) rather than churning.

configure() {
    set -e
    # Build from bundled source (no network), install to /usr. A portable
    # multi-target sysimage so the shipped julia runs across heterogeneous CPUs.
    # USE_SYSTEM_GMP=1 (first build-verify catch): bundled gmp-6.3.0's
    # "long long reliability" configure probe uses pre-C23 semantics and gcc 15
    # (default gnu23) hard-errors it -> "could not find a working compiler".
    # The system gmp is the SAME 6.3.0 already carrying the gcc-15 probe fix
    # (core/gmp configure sed) plus --enable-fat portability; the upstream-
    # documented system-gmp knob is the distro-proven path and drops a bundled
    # twin.
    cat > Make.user << 'EOF'
USE_BINARYBUILDER=0
USE_SYSTEM_GMP=1
prefix=/usr
JULIA_CPU_TARGET=generic;native
EOF
}

# GCC-15 / libstdc++-15 <cstdint> compatibility (second build-verify catch).
# The bundled LLVM fork (JuliaLang/llvm-project julia-18.1.7-4, an LLVM 18.1.7
# derivative) predates the libstdc++-15 change that stopped transitively
# including <cstdint>. Its headers — ADT/SmallVector.h and the X86/AMDGPU
# MCTargetDesc descs, plus the TableGen-generated *GenRegisterInfo.inc — use
# uint32_t/uint64_t with no explicit include, so under gcc 15 they fail with
# "'uint64_t' was not declared in this scope". Upstream fixed this in the LLVM
# 19 cycle (PRs 101761 / 101766 / 123320); the fixes are not on release/18.x.
#   Fix: force-include <cstdint> into every C++ translation unit through the
#   environment CXXFLAGS. Julia keeps its own flags in JCXXFLAGS and forwards
#   CXXFLAGS to each bundled dependency's build; for LLVM, deps/llvm.mk does
#   `LLVM_CXXFLAGS += $(CXXFLAGS)` -> -DCMAKE_CXX_FLAGS, so the flag reaches the
#   LLVM compile (CXXFLAGS drives CMAKE_CXX_FLAGS only — C++ TUs; cstdint is a
#   C++ header). One force-include also covers the generated .inc units a
#   per-header patch set would miss, and is immune to the fork's own header edits.
#   Provenance: this is exactly how the same bundled fork is built under gcc 15
#   by Fedora (rpms/julia julia.spec: build_cxxflags += -include cstdint; ref
#   JuliaLang/julia#58478). Chosen over staged header patches for the reasons
#   above; the header-patch route (fork-rebased commits 20dbc097 SmallVector /
#   7b286558 AMDGPU / 18021ff5 X86) is the documented alternative.
build() {
    set -e
    export CXXFLAGS="${CXXFLAGS:+$CXXFLAGS }-include cstdint"
    # env -u DESTDIR (third build-verify catch): the builder exports
    # do_install's staging DESTDIR for the whole package run, and the bundled
    # llvm-julia fork's cmake install honors it — deps/llvm.mk's staged install
    # lands under the staging root instead of usr-staging/<fork>/build_Release/
    # <abs-path>, so the .mk's follow-up copy fails ("cannot stat …/usr/bin/
    # lld"). Same class as the zig/ghc build-phase DESTDIR-redirect guards;
    # do_install's explicit DESTDIR= install below is the one place it belongs.
    env -u DESTDIR make -j"$(nproc)"
}

check() {
    set -e
    ./julia -e 'println("julia ", VERSION, " OK")'
}

do_install() {
    set -e
    make install DESTDIR="${DESTDIR}"
}
