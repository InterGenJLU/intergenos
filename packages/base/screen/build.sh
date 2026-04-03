#!/bin/bash
# GNU Screen 5.0.1 — Terminal multiplexer
# BLFS 13.0

configure() {
    # Fix info page build issue
    sed 's/\([a-z]\)@opensuse/\1@@opensuse/' -i doc/screen.texinfo

    ./configure --prefix=/usr                   \
                --infodir=/usr/share/info       \
                --mandir=/usr/share/man         \
                --enable-pam                    \
                --enable-socket-dir=/run/screen \
                --with-pty-group=5              \
                --with-system_screenrc=/etc/screenrc

    sed -i -e "s%/usr/local/etc/screenrc%/etc/screenrc%" {etc,doc}/*
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    # Create /etc in DESTDIR before make install — the Makefile tries to
    # copy screenrc there during install and fails if it doesn't exist
    install -v -d -m755 "${DESTDIR}/etc"

    make DESTDIR="$DESTDIR" install

    install -v -m644 etc/etcscreenrc "${DESTDIR}/etc/screenrc"
}
