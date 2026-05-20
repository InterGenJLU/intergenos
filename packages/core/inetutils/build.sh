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

    # Set setuid bit on ping — non-root users need privileged ICMP
    # socket access. Must be set here because tar-based deployment
    # strips setuid bits during extraction (pkm restores them from
    # tarball metadata post-extract; see pkm/installer.py:475-490).
    # Alternative: file capabilities (cap_net_raw,cap_net_admin+ep)
    # — not adopted here because our pipeline preserves setuid via
    # tarball metadata but does not yet preserve xattr-based caps
    # end-to-end. Revisit if/when xattr preservation lands.
    chmod 4755 "${DESTDIR}/usr/bin/ping"
}
