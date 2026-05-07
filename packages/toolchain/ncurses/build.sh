#!/bin/bash
# Ncurses 6.6
# LFS 13.0 Section 6.3
#
# Ncurses requires special handling: build tic for the host first,
# install it to $IGOS/tools, then cross-compile the library for the target.

configure() {
    set -e
    # Build tic for the host system first and install to tools
    mkdir -v build
    pushd build
        ../configure --prefix=$IGOS/tools AWK=gawk
        make -C include
        make -C progs tic
        install progs/tic $IGOS/tools/bin
    popd

    # Now configure for cross-compilation
    ./configure                          \
        --prefix=/usr                    \
        --host=$IGOS_TARGET              \
        --build=$(./config.guess)        \
        --mandir=/usr/share/man          \
        --with-manpage-format=normal     \
        --with-shared                    \
        --without-normal                 \
        --with-cxx-shared                \
        --without-debug                  \
        --without-ada                    \
        --disable-stripping              \
        AWK=gawk
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

install() {
    set -e
    make DESTDIR=$IGOS TIC_PATH=$(pwd)/build/progs/tic install
    ln -sv libncursesw.so $IGOS/usr/lib/libncurses.so
    sed -e 's/^#if.*XOPEN.*$/#if 1/' -i $IGOS/usr/include/curses.h
}
