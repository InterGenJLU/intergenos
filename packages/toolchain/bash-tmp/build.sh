#!/bin/bash
# bash 5.3 (temporary tools)
# LFS 13.0 Section 6.4

configure() {
    ./configure --prefix=/usr                   \
                --build=$(sh support/config.guess) \
                --host=$IGOS_TARGET             \
                --without-bash-malloc
}

build() {
    make -j${IGOS_JOBS}
}

install() {
    make DESTDIR=$IGOS install
    ln -sv bash $IGOS/bin/sh
}
