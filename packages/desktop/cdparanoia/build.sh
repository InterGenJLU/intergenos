#!/bin/bash
# cdparanoia 10.2 — CD audio extraction tool
# BLFS 13.0

configure() {
    # cdparanoia uses -fpic (lowercase) which is insufficient for shared
    # libraries on x86_64 with GCC 15. Force -fPIC via CFLAGS.
    export CFLAGS="-O2 -fPIC"
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    # cdparanoia's Makefile has no DESTDIR support — redirect install
    # paths manually to the staging directory
    make install \
        BINDIR="${DESTDIR}/usr/bin" \
        MANDIR="${DESTDIR}/usr/share/man" \
        INCLUDEDIR="${DESTDIR}/usr/include/cdda" \
        LIBDIR="${DESTDIR}/usr/lib"

}
