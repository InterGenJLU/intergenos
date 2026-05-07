#!/bin/bash
# GCC 15.2.0 — Pass 2
# LFS 13.0 Section 6.18
#
# Cross-compiled GCC for the target system. Includes POSIX threads
# support and builds both C and C++ compilers.

configure() {
    set -e
    # On x86_64: set default directory name for 64-bit libraries to "lib"
    case $(uname -m) in
        x86_64)
            sed -e '/m64=/s/lib64/lib/' \
                -i.orig gcc/config/i386/t-linux64
        ;;
    esac

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
        --disable-multilib                    \
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
