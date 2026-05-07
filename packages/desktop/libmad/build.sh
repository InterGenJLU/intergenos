#!/bin/bash
# libmad 0.15.1b — High-quality MPEG audio decoder
# BLFS 13.0 (https://www.linuxfromscratch.org/blfs/view/13.0/multimedia/libmad.html)
#
# Upstream is dormant since 2004; the BLFS fixes-1 patch is required for
# modern toolchains. Stale autoconf macros and outdated configure script
# require regeneration:
#   - AM_CONFIG_HEADER → AC_CONFIG_HEADERS (autoconf >=2.70 deprecated form)
#   - touch NEWS/AUTHORS/ChangeLog so autoreconf does not error
#   - autoreconf -fi to regenerate configure for modern automake/libtool
#
# Patch is applied automatically by the build framework before configure().

configure() {
    set -e
    sed "s@AM_CONFIG_HEADER@AC_CONFIG_HEADERS@g" -i configure.ac
    touch NEWS AUTHORS ChangeLog
    autoreconf -fi

    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Upstream ships no pkg-config file; consumers (e.g. cdrdao, audacity)
    # expect mad.pc. Recipe per BLFS 13.0.
    install -dm755 "${DESTDIR}/usr/lib/pkgconfig"
    cat > "${DESTDIR}/usr/lib/pkgconfig/mad.pc" << EOF
prefix=/usr
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: mad
Description: MPEG audio decoder
Requires:
Version: ${version}
Libs: -L\${libdir} -lmad
Cflags: -I\${includedir}
EOF
}
