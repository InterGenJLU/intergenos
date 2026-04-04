#!/bin/bash
# Findutils 4.10.0
# LFS 13.0 Section 8.64

configure() {
    ./configure --prefix=/usr \
        --localstatedir=/var/lib/locate
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    if command -v su >/dev/null 2>&1 && id tester >/dev/null 2>&1; then
        chown -R tester .
        su tester -c "PATH=$PATH make check"
    else
        # su/tester not yet available (shadow not built) — run tests as root
        make check || true
    fi
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
