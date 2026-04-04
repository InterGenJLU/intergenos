#!/bin/bash
# diffutils 3.12 (temporary tools)
# LFS 13.0 Section 6.6

configure() {
    ./configure --prefix=/usr                       \
                --host=$IGOS_TARGET                 \
                --build=$(./build-aux/config.guess)  \
                gl_cv_func_strcasecmp_works=y
}

build() {
    make -j${IGOS_JOBS}
}

install() {
    make DESTDIR=$IGOS install
}
