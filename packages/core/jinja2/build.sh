#!/bin/bash
# Jinja2 3.1.6
# LFS 13.0 Section 8.77
#
# DESTDIR exception: pip uses --root instead of DESTDIR.

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

do_install() {
    set -e
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist Jinja2
}
