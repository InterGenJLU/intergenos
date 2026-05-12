#!/bin/bash
# python-pefile 2024.8.26 — PE file reader (pure Python)
# Required by systemd-pass2's ukify tool.

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
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist pefile
}
