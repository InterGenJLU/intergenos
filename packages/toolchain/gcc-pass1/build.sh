#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# GCC 15.2.0 — Pass 1 (Cross Compiler, C and C++ only)
# LFS 13.0 Section 5.3
#
# GMP, MPFR, and MPC are extracted into the GCC source tree by the
# build executor (bundled_deps in package.yml). They are built as
# part of GCC automatically.

configure() {
    set -e
    # On x86_64: 64-bit libraries live in "lib", 32-bit multilib in "lib32"
    # (Multilib-LFS 13.0 §5.3 two-expression form; GE arc, D-W0-5)
    case $(uname -m) in
        x86_64)
            sed -e '/m64=/s/lib64/lib/' \
                -e '/m32=/s/m32=.*/m32=..\/lib32$(call if_multiarch,:i386-linux-gnu)/' \
                -i.orig gcc/config/i386/t-linux64
        ;;
    esac

    # Default -m32 code to stack realignment (Multilib-LFS 13.0 §5.3; D-W0-2):
    # prebuilt 32-bit binaries (Steam/Wine era) assume a 4-byte-aligned stack
    # and SIGSEGV on SSE movaps without it. Compiler default, not per-recipe CFLAGS.
    sed '/STACK_REALIGN_DEFAULT/s/0/(!TARGET_64BIT \&\& TARGET_SSE)/' \
        -i gcc/config/i386/i386.h

    mkdir -v build
    cd       build

    # --enable-initfini-array + --disable-decimal-float per Multilib-LFS 13.0
    # §5.3 (adopted 2026-07-02; vanilla LFS carries neither). The
    # first replaces a link-and-run configure guess that cannot execute in
    # this --without-headers cross build; the second turns OFF decimal
    # floating point (default-ON for x86_64), which would otherwise build
    # unwanted DFP support into BOTH ABIs' pass-1 libgcc under multilib.
    ../configure                             \
        --target=$IGOS_TARGET                \
        --prefix=$IGOS/tools                 \
        --with-glibc-version=2.43            \
        --with-sysroot=$IGOS                 \
        --with-newlib                        \
        --without-headers                    \
        --enable-default-pie                 \
        --enable-default-ssp                 \
        --enable-initfini-array              \
        --disable-nls                        \
        --disable-shared                     \
        --enable-multilib                    \
        --with-multilib-list=m64,m32         \
        --disable-decimal-float              \
        --disable-threads                    \
        --disable-libatomic                  \
        --disable-libgomp                    \
        --disable-libquadmath                \
        --disable-libssp                     \
        --disable-libvtv                     \
        --disable-libstdcxx                  \
        --enable-languages=c,c++
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

install() {
    set -e
    cd build
    make install

    # Create full internal limits.h header
    # GCC installs a partial limits.h that doesn't include the system header.
    # This creates the full version that will be needed later.
    cd ..
    cat gcc/limitx.h gcc/glimits.h gcc/limity.h > \
        $(dirname $($IGOS_TARGET-gcc -print-libgcc-file-name))/include/limits.h
}
