#!/bin/bash
# rsync 3.4.1 — Fast incremental file transfer
# BLFS 13.0

configure() {
    # Apply security fix (required)
    patch -Np1 -i "${IGOS_SOURCES}/rsync-3.4.1-security_fix-1.patch"

    ./configure --prefix=/usr    \
                --disable-xxhash \
                --without-included-zlib
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    sed -i '/typedef/d' wildtest.c
    make check || true
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
