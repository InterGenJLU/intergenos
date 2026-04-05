#!/bin/bash
# pyyaml 6.0.3 — YAML parser and emitter for Python
# BLFS 13.0

configure() {
    :
}

build() {
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    pip3 install --no-index --find-links dist --no-user \
         --root="$DESTDIR" --prefix=/usr PyYAML
}
