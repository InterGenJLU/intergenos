#!/bin/bash
# MarkupSafe 3.0.3
# LFS 13.0 Section 8.76
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
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist MarkupSafe
}
