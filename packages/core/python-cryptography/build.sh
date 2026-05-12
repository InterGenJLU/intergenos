#!/bin/bash
# python-cryptography 44.0.0 — Python cryptographic primitives
# Required by systemd-pass2's ukify tool. Builds Rust extension against
# system OpenSSL (rust + setuptools-rust + cffi in host deps).

configure() {
    set -e
    :
}

build() {
    set -e
    # OPENSSL_NO_VENDOR=1 forces link against our system OpenSSL,
    # not a vendored copy.
    OPENSSL_NO_VENDOR=1 \
        pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

do_install() {
    set -e
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist cryptography
}
