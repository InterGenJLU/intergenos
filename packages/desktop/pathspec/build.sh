#!/bin/bash
# pathspec 1.0.4 — Utility library for gitignore style pattern matching
# BLFS 13.0

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --no-index --no-deps --find-links dist --no-user --root="$DESTDIR" pathspec
}
