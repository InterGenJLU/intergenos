#!/bin/bash
# setuptools-rust 1.10.2 — Setuptools plugin for Rust extensions

configure() {
    set -e
    :
}

build() {
    set -e
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

do_install() {
    set -e
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist setuptools-rust
}
