#!/bin/bash
# meson_python 0.19.0 — Python build backend (PEP 517) for Meson projects
# BLFS 13.0

configure() {
    set -e
    :
}

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --no-index --find-links dist --no-user \
         --root="$DESTDIR" --no-deps meson_python
}
