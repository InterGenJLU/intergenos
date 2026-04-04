#!/bin/bash
# Pcre2 10.47
# LFS 13.0 Section 8.13

configure() {
    ./configure --prefix=/usr                       \
        --docdir=/usr/share/doc/pcre2-10.47         \
        --enable-unicode                            \
        --enable-jit                                \
        --enable-pcre2-16                           \
        --enable-pcre2-32                           \
        --enable-pcre2grep-libz                     \
        --enable-pcre2grep-libbz2                   \
        --enable-pcre2test-libreadline              \
        --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
