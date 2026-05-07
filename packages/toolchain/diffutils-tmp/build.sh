#!/bin/bash
# diffutils 3.12 (temporary tools)
# LFS 13.0 Section 6.6

configure() {
    set -e
    ./configure --prefix=/usr                       \
                --host=$IGOS_TARGET                 \
                --build=$(./build-aux/config.guess)  \
                gl_cv_func_strcasecmp_works=y
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

install() {
    set -e
    make DESTDIR=$IGOS install
}
