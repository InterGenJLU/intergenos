#!/bin/bash
# aspell 0.60.8.2 — Interactive spell checking program and libraries
# BLFS 13.0

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install

    ln -svfn aspell-0.60 "${DESTDIR}/usr/lib/aspell"
    install -v -m755 -d "${DESTDIR}/usr/share/doc/aspell-0.60.8.2/aspell.html"
    install -v -m644 manual/aspell.html/* \
        "${DESTDIR}/usr/share/doc/aspell-0.60.8.2/aspell.html"
    install -v -m644 manual/{aspell,spell-checking}.pdf \
        "${DESTDIR}/usr/share/doc/aspell-0.60.8.2" 2>/dev/null || true
}

post_install() {
    # Install English dictionary
    local dict_tar="${IGOS_SOURCES}/aspell6-en-2020.12.07-0.tar.bz2"
    if [ -f "$dict_tar" ]; then
        local tmpdir=$(mktemp -d)
        tar xf "$dict_tar" -C "$tmpdir" --strip-components=1
        cd "$tmpdir"
        ./configure
        make
        make install
        cd /
        rm -rf "$tmpdir"
    fi
}
