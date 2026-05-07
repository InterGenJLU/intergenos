#!/bin/bash
# Inetutils 2.7
# LFS 13.0 Section 8.42

configure() {
    set -e
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
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    mkdir -pv "${DESTDIR}/usr/sbin"
    mv -v "${DESTDIR}/usr/bin/ifconfig" "${DESTDIR}/usr/sbin/ifconfig"
}
