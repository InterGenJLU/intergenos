#!/bin/bash
# Binutils 2.46.0 (final system)
# LFS 13.0 Section 8.21

configure() {
    set -e
    mkdir -v build
    cd       build

    ../configure --prefix=/usr       \
        --build=x86_64-igos-linux-gnu \
        --host=x86_64-igos-linux-gnu  \
        --sysconfdir=/etc            \
        --enable-gold                \
        --enable-ld=default          \
        --enable-plugins             \
        --enable-shared              \
        --disable-werror             \
        --enable-64-bit-bfd          \
        --enable-new-dtags           \
        --with-system-zlib           \
        --enable-default-hash-style=gnu
}

build() {
    set -e
    cd build
    make -j${IGOS_JOBS} tooldir=/usr
}

check() {
    set -e
    cd build
    make -k check || true

    echo ""
    echo "=== Binutils Test Summary ==="
    grep -A7 'Summaries' $(find . -name '*.sum') | grep -E 'PASS|FAIL' || true
}

do_install() {
    set -e
    cd build
    make DESTDIR="$DESTDIR" tooldir=/usr install

    # Remove useless static libraries, .la files, and gprofng docs per LFS
    rm -fv "${DESTDIR}/usr/lib"/lib{bfd,ctf,ctf-nobfd,gprofng,opcodes,sframe}.a
    rm -fv "${DESTDIR}/usr/lib"/lib{bfd,ctf,ctf-nobfd,gprofng,opcodes,sframe}.la
    rm -rfv "${DESTDIR}/usr/share/doc/gprofng/"
}
