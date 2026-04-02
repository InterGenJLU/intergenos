#!/bin/bash
# setuptools-scm 8.1.0 — Setuptools SCM plugin
# BLFS 13.0

configure() { : ; }

build() {
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    pip3 install --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" setuptools_scm
}
