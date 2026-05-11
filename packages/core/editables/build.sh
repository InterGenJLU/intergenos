#!/bin/bash
# editables 0.5 — Python editable installs helper
# BLFS 13.0

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" editables
}
