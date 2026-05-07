#!/bin/bash
# fuse-overlayfs 1.16 — Overlay filesystem in userspace
# Not in BLFS — InterGenOS extra tier
#
# FUSE implementation of overlayfs for rootless containers.
# C project using autotools build system.
# Links against libfuse3. Produces a single binary: fuse-overlayfs.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    ./autogen.sh
    ./configure --prefix=/usr
}

build() {
    set -e
    make
}

check() {
    set -e
    ./fuse-overlayfs --version
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    install -d "$DESTDIR/usr/share/man/man1"
    install -v -m644 "$BUILD_DIR/fuse-overlayfs.1" \
        "$DESTDIR/usr/share/man/man1/fuse-overlayfs.1"
}
