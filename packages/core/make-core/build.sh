#!/bin/bash
# Make 4.4.1
# LFS 13.0 Section 8.71

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    if command -v su >/dev/null 2>&1 && id tester >/dev/null 2>&1; then
        chown -R tester .
        su tester -c "PATH=$PATH make check"
    else
        make check
    fi
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
