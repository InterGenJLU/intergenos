#!/bin/bash
# Ncurses 6.6
# LFS 13.0 Section 8.31
#
# Ncurses has a unique install pattern — LFS already uses DESTDIR=$PWD/dest
# to avoid crashing the running shell that depends on ncurses. We adapt this
# to stage into $DESTDIR instead.

configure() {
    ./configure --prefix=/usr           \
        --mandir=/usr/share/man         \
        --with-shared                   \
        --without-debug                 \
        --without-normal                \
        --with-cxx-shared               \
        --enable-pc-files               \
        --with-pkg-config-libdir=/usr/lib/pkgconfig
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    # Stage into a local dest first (LFS pattern), then copy to $DESTDIR
    make DESTDIR=$PWD/dest install

    # Fix header for wide-character compatibility
    sed -e 's/^#if.*XOPEN.*$/#if 1/' -i dest/usr/include/curses.h

    # Copy staged files to our DESTDIR
    mkdir -pv "$DESTDIR"
    cp --remove-destination -av dest/* "$DESTDIR"

    # Compatibility symlinks for non-wide-character programs
    for lib in ncurses form panel menu; do
        ln -sfv lib${lib}w.so "${DESTDIR}/usr/lib/lib${lib}.so"
        ln -sfv ${lib}w.pc "${DESTDIR}/usr/lib/pkgconfig/${lib}.pc"
    done

    # Ensure old -lcurses apps still build
    ln -sfv libncursesw.so "${DESTDIR}/usr/lib/libcurses.so"

    # Remove static libraries
    rm -fv "${DESTDIR}/usr/lib/libncursesw.a"
    rm -fv "${DESTDIR}/usr/lib"/lib{ncurses,form,panel,menu}.a
}
