#!/bin/bash
# Man-DB 2.13.1
# LFS 13.0 Section 8.80

configure() {
    ./configure --prefix=/usr                         \
        --libdir=/usr/lib                             \
        --docdir=/usr/share/doc/man-db-2.13.1         \
        --sysconfdir=/etc                             \
        --disable-setuid                              \
        --enable-cache-owner=bin                      \
        --with-browser=/usr/bin/lynx                  \
        --with-vgrind=/usr/bin/vgrind                 \
        --with-grap=/usr/bin/grap
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install

    # Move misplaced /lib files to /usr/lib (libtool ignores --libdir sometimes)
    if [ -d "${DESTDIR}/lib" ]; then
        mkdir -p "${DESTDIR}/usr/lib"
        cp -a "${DESTDIR}/lib"/* "${DESTDIR}/usr/lib/" 2>/dev/null || true
        rm -rf "${DESTDIR}/lib"
    fi
}
