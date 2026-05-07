#!/bin/bash
# opusfile 0.12 — Decoder for Opus audio in the Ogg container
#
# Notes
# - Mandatory PKG_CHECK_MODULES: ogg >= 1.3, opus >= 1.0.1.
# - openssl is pulled in for HTTP-stream support (--enable-http is the
#   default); we keep that on so apps like Audacity can stream Opus.
# - Pure autotools; no special quirks.

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
