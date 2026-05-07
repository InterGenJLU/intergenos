#!/bin/bash
# Util-linux 2.41.3 (temporary tools)
# LFS 13.0 Section 7.11

configure() {
    set -e
    mkdir -pv $IGOS/var/lib/hwclock

    ./configure                       \
        --libdir=/usr/lib             \
        --runstatedir=/run            \
        --disable-chfn-chsh           \
        --disable-login               \
        --disable-nologin             \
        --disable-su                  \
        --disable-setpriv             \
        --disable-runuser             \
        --disable-pylibmount          \
        --disable-liblastlog2         \
        --disable-static              \
        --without-python              \
        ADJTIME_PATH=/var/lib/hwclock/adjtime \
        --docdir=/usr/share/doc/util-linux-${version}
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

install() {
    set -e
    make DESTDIR=$IGOS install
}
