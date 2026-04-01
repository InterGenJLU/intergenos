#!/bin/bash
# Inetutils 2.7
# LFS 13.0 Section 8.42

configure() {
    # Fix building with gcc-14.1 or later
    sed -i 's/def HAVE_TERMCAP_TGETENT/ 1/' telnet/telnet.c

    ./configure --prefix=/usr        \
        --bindir=/usr/bin            \
        --localstatedir=/var         \
        --disable-logger             \
        --disable-whois              \
        --disable-rcp                \
        --disable-rexec              \
        --disable-rlogin             \
        --disable-rsh                \
        --disable-servers
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check
}

install() {
    make DESTDIR="$DESTDIR" install
    mv -v "${DESTDIR}/usr/"{,s}bin/ifconfig
}
