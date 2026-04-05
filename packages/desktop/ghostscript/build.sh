#!/bin/bash
# ghostscript 10.06.0 — Interpreter for PostScript and PDF
# BLFS 13.0

configure() {
    # Remove bundled copies of libraries we have system versions of
    rm -rf freetype lcms2mt jpeg libpng openjpeg zlib

    ./configure --prefix=/usr           \
                --disable-compile-inits \
                --with-system-libtiff   \
                CFLAGS="${CFLAGS:--g -O3} -fPIC"
}

build() {
    make -j${IGOS_JOBS}
    # Build shared library (libgs.so)
    make so
}

do_install() {
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" soinstall

    # Install headers for programs that link to libgs
    install -v -m644 base/*.h "${DESTDIR}/usr/include/ghostscript/"
    ln -sfvn ghostscript "${DESTDIR}/usr/include/ps"
}

post_install() {
    # Fix documentation directory name
    if [ -d /usr/share/doc/ghostscript/${PKG_VERSION} ]; then
        mv -v /usr/share/doc/ghostscript/${PKG_VERSION} \
              /usr/share/doc/ghostscript-${PKG_VERSION}
        rmdir /usr/share/doc/ghostscript 2>/dev/null || true
    fi
}
