#!/bin/bash
# bash 5.3 (temporary tools)
# LFS 13.0 Section 6.4

configure() {
    set -e
    ./configure --prefix=/usr                   \
                --build=$(sh support/config.guess) \
                --host=$IGOS_TARGET             \
                --without-bash-malloc
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

install() {
    set -e
    make DESTDIR=$IGOS install
    ln -sv bash $IGOS/bin/sh
}
