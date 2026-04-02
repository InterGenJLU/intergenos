#!/bin/bash
# Git 2.53.0 — Distributed version control
# BLFS 13.0

configure() {
    ./configure --prefix=/usr                   \
                --with-gitconfig=/etc/gitconfig \
                --with-python=python3           \
                --with-libpcre2
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    GIT_UNZIP=nonexist make test -k || true
}

do_install() {
    make DESTDIR="$DESTDIR" perllibdir=/usr/lib/perl5/5.42/site_perl install

    # Install pre-built man pages
    tar -xf ${IGOS_SOURCES}/git-manpages-2.53.0.tar.xz \
        -C "${DESTDIR}/usr/share/man" --no-same-owner --no-overwrite-dir
}
