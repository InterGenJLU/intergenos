#!/bin/bash
# conmon 2.2.1 — OCI container runtime monitor
# Not in BLFS — InterGenOS extra tier
#
# Monitors container processes for OCI runtimes (crun, runc).
# Handles logging, terminal attach/detach, and exit-code forwarding.
# Requires glib-2.0; optionally links libsystemd and libseccomp.
#
# Man page pre-generated from upstream docs/conmon.8.md via go-md2man
# and shipped as a static file in the package directory.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    :
}

build() {
    make PREFIX=/usr
}

check() {
    bin/conmon --version
}

do_install() {
    make PREFIX=/usr DESTDIR="$DESTDIR" install.bin
    install -d "$DESTDIR/usr/share/man/man8"
    install -v -m644 "$BUILD_DIR/conmon.8" "$DESTDIR/usr/share/man/man8/conmon.8"
}
