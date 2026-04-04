#!/bin/bash
# Tcl 8.6.17
# LFS 13.0 Section 8.17
#
# Test infrastructure — needed for Binutils, GCC, and other test suites.

configure() {
    cd unix
    ./configure --prefix=/usr           \
        --mandir=/usr/share/man         \
        --disable-rpath
}

build() {
    # SRCDIR must be computed here — each phase runs in a separate subprocess
    SRCDIR=$(dirname $(pwd))
    cd unix
    make -j${IGOS_JOBS}

    # Fix references to build directory in config files
    sed -e "s|$SRCDIR/unix|/usr/lib|" \
        -e "s|$SRCDIR|/usr/include|"  \
        -i tclConfig.sh

    sed -e "s|$SRCDIR/unix/pkgs/tdbc1.1.12|/usr/lib/tdbc1.1.12|" \
        -e "s|$SRCDIR/pkgs/tdbc1.1.12/generic|/usr/include|"     \
        -e "s|$SRCDIR/pkgs/tdbc1.1.12/library|/usr/lib/tcl8.6|"  \
        -e "s|$SRCDIR/pkgs/tdbc1.1.12|/usr/include|"             \
        -i pkgs/tdbc1.1.12/tdbcConfig.sh

    sed -e "s|$SRCDIR/unix/pkgs/itcl4.3.4|/usr/lib/itcl4.3.4|" \
        -e "s|$SRCDIR/pkgs/itcl4.3.4/generic|/usr/include|"    \
        -e "s|$SRCDIR/pkgs/itcl4.3.4|/usr/include|"            \
        -i pkgs/itcl4.3.4/itclConfig.sh
}

check() {
    cd unix
    LC_ALL=C.UTF-8 make test
}

do_install() {
    cd unix
    make DESTDIR="$DESTDIR" install

    chmod 644 "${DESTDIR}/usr/lib/libtclstub8.6.a"
    chmod -v u+w "${DESTDIR}/usr/lib/libtcl8.6.so"

    make DESTDIR="$DESTDIR" install-private-headers
    ln -sfv tclsh8.6 "${DESTDIR}/usr/bin/tclsh"

    # Rename Thread man page to avoid conflict
    mv -v "${DESTDIR}/usr/share/man/man3/Thread.3" \
          "${DESTDIR}/usr/share/man/man3/Tcl_Thread.3"
}
