#!/bin/bash
# Man-pages 6.17
# LFS 13.0 Section 8.3
#
# No configure or build step. Just removes conflicting pages and installs.

configure() {
    set -e
    # Remove crypt man pages — libxcrypt provides better versions
    rm -v man3/crypt*
}

build() {
    set -e
    : # Nothing to build
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" -R GIT=false prefix=/usr install
}
