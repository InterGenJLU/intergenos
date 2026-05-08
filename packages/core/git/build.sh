#!/bin/bash
# Git 2.53.0 — Distributed version control
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr                   \
                --with-gitconfig=/etc/gitconfig \
                --with-python=python3           \
                --with-libpcre2
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    GIT_UNZIP=nonexist pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make test -k
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" perllibdir=/usr/lib/perl5/5.42/site_perl install

    # Install pre-built man pages
    install -v -d -m755 "${DESTDIR}/usr/share/man"
    tar -xf "${IGOS_SOURCES}/git-manpages-${PKG_VERSION}.tar.xz" \
        -C "${DESTDIR}/usr/share/man" --no-same-owner --no-overwrite-dir
}
