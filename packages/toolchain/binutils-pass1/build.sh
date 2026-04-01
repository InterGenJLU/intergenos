#!/bin/bash
# Binutils 2.46 — Pass 1 (Cross Binutils)
# LFS 13.0 Section 5.2
#
# Binutils MUST be the first package compiled because both
# Glibc and GCC perform tests on the available linker and
# assembler to determine which of their own features to enable.

configure() {
    mkdir -v build
    cd       build

    ../configure                             \
        --prefix=$IGOS/tools                 \
        --with-sysroot=$IGOS                 \
        --target=$IGOS_TARGET                \
        --disable-nls                        \
        --enable-gprofng=no                  \
        --disable-werror                     \
        --enable-new-dtags                   \
        --enable-default-hash-style=gnu
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

install() {
    cd build
    make install
}
