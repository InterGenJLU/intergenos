#!/bin/bash
# hatch-fancy-pypi-readme 25.1.0 — Hatch plugin for fancy PyPI READMEs
# BLFS 13.0

configure() { : ; }

build() {
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    pip3 install --no-index --find-links dist --no-user --root="$DESTDIR" hatch_fancy_pypi_readme
}
