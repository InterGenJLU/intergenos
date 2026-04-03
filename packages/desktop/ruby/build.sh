#!/bin/bash
# ruby 4.0.1 — Ruby programming language
# BLFS 13.0

configure() {
    ./configure --prefix=/usr      \
                --enable-shared    \
                --disable-rpath    \
                --without-valgrind \
                --without-baseruby \
                ac_cv_func_qsort_r=no
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
