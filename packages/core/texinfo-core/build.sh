#!/bin/bash
# Texinfo 7.2
# LFS 13.0 Section 8.74

configure() {
    set -e
    # Fix Perl compatibility issue
    sed 's/! $output_file eq/$output_file ne/' -i tp/Texinfo/Convert/*.pm

    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" TEXMF=/usr/share/texmf install-tex
}

# Post-install: rebuild info dir on live system
post_install() {
    set -e
    pushd /usr/share/info
    rm -v dir
    for f in *; do
        install-info "$f" dir 2>/dev/null
    done
    popd
}
