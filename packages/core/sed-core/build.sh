#!/bin/bash
# Sed 4.9
# LFS 13.0 Section 8.32

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
    make html
}

check() {
    if command -v su >/dev/null 2>&1 && id tester >/dev/null 2>&1; then
        chown -R tester .
        su tester -c "PATH=$PATH make check"
    else
        make check || true
    fi
}

do_install() {
    make DESTDIR="$DESTDIR" install
    install -d -m755 "${DESTDIR}/usr/share/doc/sed-4.9"
    install -m644 doc/sed.html "${DESTDIR}/usr/share/doc/sed-4.9"
}
