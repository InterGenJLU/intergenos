#!/bin/bash
# Readline 8.3
# LFS 13.0 Section 8.12

configure() {
    # Reinstalling readline will cause old libraries to be moved to <name>.old
    # While not normally a problem, it can cause linking issues with ldconfig
    sed -i '/MV.*telerik/d' support/shlib-install

    # Fix an issue identified upstream
    sed -i 's/SHLIB_LIBS -o/SHLIB_LIBS -L/usr/lib -o/' support/shlib-install

    ./configure --prefix=/usr        \
        --disable-static             \
        --with-curses                \
        --docdir=/usr/share/doc/readline-8.3
}

build() {
    make SHLIB_LIBS="-lncursesw" -j${IGOS_JOBS}
}

install() {
    make SHLIB_LIBS="-lncursesw" install
    install -v -m644 doc/*.{ps,pdf,html,dvi} /usr/share/doc/readline-8.3
}
