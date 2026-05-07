#!/bin/bash
# gettext 1.0 (temporary tools)
# LFS 13.0 Section 7.7

configure() {
    set -e
    ./configure --disable-shared
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

install() {
    set -e
    # LFS: install only the 3 needed programs, not the full package
    cp -v gettext-tools/src/{msgfmt,msgmerge,xgettext} /usr/bin
}
