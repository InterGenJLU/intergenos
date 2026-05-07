#!/bin/bash
# File 5.46 (temporary tools)
# LFS 13.0 Section 6.6
#
# File requires a host-built version of itself during cross-compilation,
# so we must build it for the host first then cross-compile.

configure() {
    set -e
    # Build file for the host first
    mkdir -v build
    pushd build
        ../configure --disable-bzlib      \
                     --disable-libseccomp \
                     --disable-xzlib      \
                     --disable-zlib
        make
    popd

    # Now cross-compile for target
    ./configure --prefix=/usr        \
        --host=$IGOS_TARGET          \
        --build=$(./config.guess)
}

build() {
    set -e
    make FILE_COMPILE=$(pwd)/build/src/file -j${IGOS_JOBS}
}

install() {
    set -e
    make DESTDIR=$IGOS install
    rm -v $IGOS/usr/lib/libmagic.la
}
