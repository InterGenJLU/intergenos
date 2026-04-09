#!/bin/bash
# unixodbc 2.3.14 — Open Database Connectivity (ODBC) implementation for Unix
# BLFS 13.0

configure() {
    # BLFS: regenerate build system (GitHub archive, not release tarball)
    autoreconf -fiv

    ./configure --prefix=/usr           \
                --sysconfdir=/etc/unixODBC \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # BLFS: install documentation
    find doc -name "Makefile*" -delete
    chmod 644 doc/{lst,ProgrammerManual/Tutorial}/*

    install -v -m755 -d "$DESTDIR/usr/share/doc/unixODBC-2.3.14"
    cp      -v -R doc/* "$DESTDIR/usr/share/doc/unixODBC-2.3.14"
}
