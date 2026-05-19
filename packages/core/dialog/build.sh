#!/bin/bash
# dialog 1.3-20260107 — TUI dialog widget library + binary.
# T0-3 sub-cluster 1 — installer TUI runtime dep (installer/frontend/tui.py).
# Flag set mirrors Debian's upstream packaging (Thomas Dickey is both dialog
# upstream + Debian packager — package/debian/rules in the source tree is the
# canonical reference). We drop Debian's --with-package/--with-program-prefix
# rename (they ship as "cdialog"; we ship as plain "dialog") and use plain
# ncursesw instead of Debian's ncursesw6td threaded-debug variant.

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-echo \
                --enable-nls \
                --with-shlib-version=abi \
                --enable-header-subdir \
                --enable-pc-files \
                --enable-stdnoreturn \
                --enable-widec \
                --with-shared \
                --with-screen=ncursesw \
                --with-versioned-syms \
                --disable-rpath-hack
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    # install + install-lib are separate targets in dialog's Makefile; both
    # required (install ships /usr/bin/dialog, install-lib ships libdialog.so +
    # headers + pkg-config files).
    make DESTDIR="$DESTDIR" install install-lib
}
