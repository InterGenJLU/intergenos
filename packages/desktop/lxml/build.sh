#!/bin/bash
# lxml 5.3.0 — Python XML processing library
# BLFS 13.0

configure() { : ; }

build() {
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    pip3 install --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" lxml
}
