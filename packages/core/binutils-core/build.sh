#!/bin/bash
# Binutils 2.46.0 (final system)
# LFS 13.0 Section 8.21

configure() {
    mkdir -v build
    cd       build

    ../configure --prefix=/usr       \
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
    cd build
    make -j${IGOS_JOBS} tooldir=/usr
}

check() {
    cd build
    make -k check || true

    echo ""
    echo "=== Binutils Test Summary ==="
    grep -A7 'Summaries' $(find . -name '*.sum') | grep -E 'PASS|FAIL' || true
}

install() {
    cd build
    make tooldir=/usr install

    # Remove useless static libraries and .la files
    rm -fv /usr/lib/lib{bfd,ctf,ctf-nobfd,gprofng,opcodes,sframe}.a
    rm -fv /usr/lib/lib{bfd,ctf,ctf-nobfd,gprofng,opcodes,sframe}.la
}
