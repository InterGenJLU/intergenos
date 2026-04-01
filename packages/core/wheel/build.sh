#!/bin/bash
# Wheel 0.46.3
# LFS 13.0 Section 8.56
#
# DESTDIR exception: pip uses --root instead of DESTDIR.

configure() {
    : # No configure step
}

build() {
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

install() {
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist wheel
}
