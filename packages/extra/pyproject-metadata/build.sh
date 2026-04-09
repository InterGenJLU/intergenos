#!/bin/bash
# pyproject-metadata 0.11.0 — PEP 621 metadata class with core metadata generation
# BLFS 13.0

configure() {
    :
}

build() {
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    pip3 install --no-index --find-links dist --no-user \
         --root="$DESTDIR" --no-deps pyproject-metadata
}
