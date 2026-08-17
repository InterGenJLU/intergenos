#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# GCC 15.2.0 — Pass 2
# LFS 13.0 Section 6.18
#
# Cross-compiled GCC for the target system. Includes POSIX threads
# support and builds both C and C++ compilers.

configure() {
    set -e
    # On x86_64: 64-bit libraries live in "lib", 32-bit multilib in "lib32"
    # (Multilib-LFS 13.0 §6.18 two-expression form; GE arc, D-W0-5)
    case $(uname -m) in
        x86_64)
            sed -e '/m64=/s/lib64/lib/' \
                -e '/m32=/s/m32=.*/m32=..\/lib32$(call if_multiarch,:i386-linux-gnu)/' \
                -i.orig gcc/config/i386/t-linux64
        ;;
    esac

    # Default -m32 code to stack realignment (Multilib-LFS 13.0 §6.18; D-W0-2):
    # prebuilt 32-bit binaries (Steam/Wine era) assume a 4-byte-aligned stack
    # and SIGSEGV on SSE movaps without it. Compiler default, not per-recipe CFLAGS.
    sed '/STACK_REALIGN_DEFAULT/s/0/(!TARGET_64BIT \&\& TARGET_SSE)/' \
        -i gcc/config/i386/i386.h

    # Override build rules to allow POSIX threads support
    sed '/thread_header =/s/@.*@/gthr-posix.h/' \
        -i libgcc/Makefile.in libstdc++-v3/include/Makefile.in

    mkdir -v build
    cd       build

    ../configure                              \
        --build=$(../config.guess)            \
        --host=$IGOS_TARGET                   \
        --target=$IGOS_TARGET                 \
        --prefix=/usr                         \
        --with-build-sysroot=$IGOS            \
        --enable-default-pie                  \
        --enable-default-ssp                  \
        --disable-nls                         \
        --enable-multilib                     \
        --with-multilib-list=m64,m32          \
        --disable-libatomic                   \
        --disable-libgomp                     \
        --disable-libquadmath                 \
        --disable-libsanitizer                \
        --disable-libssp                      \
        --disable-libvtv                      \
        --enable-languages=c,c++              \
        LDFLAGS_FOR_TARGET=-L$PWD/$IGOS_TARGET/libgcc
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS}
}

install() {
    set -e
    cd build
    make DESTDIR=$IGOS install

    # Create cc symlink (many scripts refer to cc instead of gcc)
    ln -sv gcc $IGOS/usr/bin/cc
}
