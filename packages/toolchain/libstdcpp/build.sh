#!/bin/bash
# Libstdc++ from GCC 15.2.0
# LFS 13.0 Section 5.6
#
# Libstdc++ is the standard C++ library. It was deferred from gcc-pass1
# because it depends on Glibc, which was not yet available in the target.
# We build it from the same GCC source tree.

configure() {
    mkdir -v build
    cd       build

    ../libstdc++-v3/configure            \
        --host=$IGOS_TARGET              \
        --build=$(../config.guess)       \
        --prefix=/usr                    \
        --disable-multilib               \
        --disable-nls                    \
        --disable-libstdcxx-pch          \
        --with-gxx-include-dir=/tools/$IGOS_TARGET/include/c++/$PKG_VERSION
}

build() {
    cd build
    make -j${IGOS_JOBS}
}

install() {
    cd build
    make DESTDIR=$IGOS install

    # Remove libtool archives that are harmful for cross-compilation
    rm -v $IGOS/usr/lib/lib{stdc++{,exp,fs},supc++}.la
}
