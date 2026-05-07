#!/bin/bash
# coreutils 9.10 (temporary tools)
# LFS 13.0 Section 6.5

configure() {
    set -e
    ./configure --prefix=/usr                      \
                --host=$IGOS_TARGET                \
                --build=$(build-aux/config.guess)   \
                --enable-install-program=hostname   \
                --enable-no-install-program=kill,uptime
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

install() {
    set -e
    make DESTDIR=$IGOS install
    mv -v $IGOS/usr/bin/chroot $IGOS/usr/sbin
    mkdir -pv $IGOS/usr/share/man/man8
    mv -v $IGOS/usr/share/man/man1/chroot.1 $IGOS/usr/share/man/man8/chroot.8
    sed -i 's/"1"/"8"/' $IGOS/usr/share/man/man8/chroot.8
}
