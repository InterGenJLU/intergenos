#!/bin/bash
# GMP 6.3.0
# LFS 13.0 Section 8.22

configure() {
    # Fix for gcc-15 compatibility
    sed -i '/long long t1;/,+1s/()/(...)/' configure

    ./configure --prefix=/usr    \
        --enable-cxx             \
        --disable-static         \
        --docdir=/usr/share/doc/gmp-6.3.0
}

build() {
    make -j${IGOS_JOBS}
    make html
}

check() {
    make check 2>&1 | tee gmp-check-log

    # Verify all 199 tests pass
    echo ""
    echo "=== GMP Test Summary ==="
    awk '/# PASS:/{total+=$3} ; END{print "Total tests passed:",total}' gmp-check-log
}

do_install() {
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" install-html
}
