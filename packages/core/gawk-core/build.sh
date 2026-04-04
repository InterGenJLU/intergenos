#!/bin/bash
# Gawk 5.3.2
# LFS 13.0 Section 8.63

configure() {
    # Remove extras directory (non-essential)
    sed -i 's/extras//' Makefile.in

    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    if command -v su >/dev/null 2>&1 && id tester >/dev/null 2>&1; then
        chown -R tester .
        su tester -c "PATH=$PATH make check"
    else
        make check
    fi
}

do_install() {
    rm -f /usr/bin/gawk-5.3.2
    make DESTDIR="$DESTDIR" install
    ln -sv gawk.1 "${DESTDIR}/usr/share/man/man1/awk.1"
}
