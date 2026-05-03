#!/bin/bash
# libid3tag 0.15.1b — ID3 tag manipulation library
# Upstream: https://sourceforge.net/projects/mad/ (dormant since 2004)
#
# Stale autoconf macros and outdated configure script require regeneration:
#   - AM_CONFIG_HEADER → AC_CONFIG_HEADERS (autoconf >=2.70 deprecated form)
#   - touch NEWS/AUTHORS/ChangeLog so autoreconf does not error
#   - autoreconf -fi to regenerate configure for modern automake/libtool

configure() {
    sed "s@AM_CONFIG_HEADER@AC_CONFIG_HEADERS@g" -i configure.ac
    touch NEWS AUTHORS ChangeLog
    autoreconf -fi

    ./configure --prefix=/usr \
                --disable-static
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Many consumers expect a pkg-config file for libid3tag; upstream ships none.
    install -dm755 "${DESTDIR}/usr/lib/pkgconfig"
    cat > "${DESTDIR}/usr/lib/pkgconfig/id3tag.pc" << EOF
prefix=/usr
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: id3tag
Description: ID3 tag manipulation library
Requires:
Version: ${version}
Libs: -L\${libdir} -lid3tag -lz
Cflags: -I\${includedir}
EOF
}
