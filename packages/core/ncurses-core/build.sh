#!/bin/bash
# Ncurses 6.6
# LFS 13.0 Section 8.31

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

install() {
    make DESTDIR=$PWD/dest install
    install -vm755 dest/usr/lib/libncursesw.so.6.6 /usr/lib
    rm -v dest/usr/lib/libncursesw.so.6.6
    sed -e 's/^#if.*XOPEN.*$/#if 1/' -i dest/usr/include/curses.h
    cp -av dest/* /

    # Compatibility symlinks for non-wide-character programs
    for lib in ncurses form panel menu; do
        ln -sfv lib${lib}w.so /usr/lib/lib${lib}.so
        ln -sfv ${lib}w.pc /usr/lib/pkgconfig/${lib}.pc
    done

    # Ensure old -lcurses apps still build
    ln -sfv libncursesw.so /usr/lib/libcurses.so

    # Remove static libraries
    rm -fv /usr/lib/libncursesw.a
    rm -fv /usr/lib/lib{ncurses,form,panel,menu}.a
}
