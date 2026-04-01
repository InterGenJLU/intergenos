#!/bin/bash
# Readline 8.3
# LFS 13.0 Section 8.12

configure() {
    # Prevent old libraries from being moved to .old (triggers ldconfig bug)
    sed -i '/MV.*old/d' Makefile.in
    sed -i '/{OLDSUFF}/c:' support/shlib-install

    # Remove rpath from shared libraries (not needed, potential security issue)
    sed -i 's/-Wl,-rpath,[^ ]*//' support/shobj-conf

    # Fix upstream issue specific to this version
    sed -e '270a\
     else\
       chars_avail = 1;'      \
        -e '288i\   result = -1;' \
        -i.orig input.c

    ./configure --prefix=/usr    \
        --disable-static         \
        --with-curses            \
        --docdir=/usr/share/doc/readline-8.3
}

build() {
    make SHLIB_LIBS="-lncursesw" -j${IGOS_JOBS}
}

do_install() {
    make SHLIB_LIBS="-lncursesw" DESTDIR="$DESTDIR" install
    install -v -dm755 "${DESTDIR}/usr/share/doc/readline-8.3"
    install -v -m644 doc/*.{ps,pdf,html,dvi} "${DESTDIR}/usr/share/doc/readline-8.3"
}
