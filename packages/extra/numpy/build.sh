#!/bin/bash
# numpy 2.4.2 — Fundamental package for scientific computing with Python
# BLFS 13.0

configure() {
    :
}

build() {
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir \
         -C setup-args=-Dallow-noblas=true $PWD
}

do_install() {
    pip3 install --no-index --find-links dist --no-user \
         --root="$DESTDIR" --no-deps numpy
}
