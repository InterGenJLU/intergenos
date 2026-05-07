#!/bin/bash
# Expect 5.45.4
# LFS 13.0 Section 8.18

configure() {
    set -e
    # Verify PTY devices work (must output "ok")
    python3 -c 'from pty import spawn; spawn(["echo", "ok"])'

    # Apply gcc-15 compatibility patch
    patch -Np1 -i ${IGOS_PATCHES}/expect-5.45.4-gcc15-1.patch

    ./configure --prefix=/usr           \
        --with-tcl=/usr/lib             \
        --enable-shared                 \
        --disable-rpath                 \
        --mandir=/usr/share/man         \
        --with-tclinclude=/usr/include
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    ln -svf expect5.45.4/libexpect5.45.4.so "${DESTDIR}/usr/lib"
}
