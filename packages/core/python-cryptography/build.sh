#!/bin/bash
# python-cryptography 44.0.0 — Python cryptographic primitives
# Required by systemd-pass2's ukify tool. Builds Rust extension against
# system OpenSSL (rust + cffi + maturin in build deps).
#
# Vendored crates: cryptography ships its own Cargo.lock + workspace
# under src/rust/ (cryptography-cffi/keepalive/key-parsing/openssl/
# x509/x509-verification sub-crates). maturin invokes cargo internally
# during the PEP 517 build; without vendored crates cargo would try to
# fetch from crates.io (no chroot network). Vendor tarball generated
# by scripts/cargo-vendor-gen.sh.

configure() {
    set -e
    # cryptography ships its own Cargo.lock at root (origin=upstream).
    # Extract vendored crates (built offline on host).
    tar xf "${IGOS_SOURCES}/cryptography-${PKG_VERSION}-vendor.tar.xz" \
        --strip-components=1
}

build() {
    set -e
    # OPENSSL_NO_VENDOR=1 forces link against our system OpenSSL,
    # not a vendored copy.
    # CARGO_NET_OFFLINE=true belt-and-suspenders alongside the
    # .cargo/config.toml from the vendor tarball (which already
    # redirects [source.crates-io] to vendored-sources).
    OPENSSL_NO_VENDOR=1 \
    CARGO_NET_OFFLINE=true \
        pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

do_install() {
    set -e
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist cryptography
}
