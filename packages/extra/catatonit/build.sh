#!/bin/bash
# catatonit 0.2.1 — Brain-dead simple container init
# Not in BLFS — InterGenOS extra tier
#
# Single source file (catatonit.c), statically linked (-all-static).
# Used as PID 1 inside containers; handles signal forwarding and
# zombie reaping. Can be symlinked as docker-init or podman-init.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    ./autogen.sh
    ./configure --prefix=/usr
}

build() {
    make
}

check() {
    ./catatonit --version
}

do_install() {
    make DESTDIR="$DESTDIR" install
    install -d "$DESTDIR/usr/share/man/man1"
    install -v -m644 "$BUILD_DIR/catatonit.1" "$DESTDIR/usr/share/man/man1/catatonit.1"
}
