#!/bin/bash
# docutils 0.22.4 — Python documentation utilities
# BLFS 13.0

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" docutils
}
