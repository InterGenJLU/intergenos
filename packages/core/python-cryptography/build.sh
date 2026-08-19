#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
    pip3 install --ignore-installed --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist cryptography

    # The wheel this recipe builds carries the sdist's whole top level, not
    # just the library: tests/, docs/, rust/, vendor/, _cffi_src/, the Cargo
    # files and the licence files all land directly in site-packages, which
    # is a namespace shared by every Python package on the system. Two things
    # follow, and one of them was measured:
    #
    #   * `import tests`, `import docs`, `import vendor` and `import rust`
    #     resolve to this package's build tree on an installed system.
    #   * a later package shipping its own generic tests/ directory overwrote
    #     site-packages/tests/conftest.py, and the pre-capture metadata-sync
    #     gate refused the image on the resulting co-ownership split (one of
    #     three packages it named on the first release candidate's build).
    #
    # What the package legitimately owns is the cryptography/ module and its
    # dist-info. Everything else is removed here, by name — no globbing, so
    # a stray this list does not know about cannot be swept away silently.
    local sp="${DESTDIR}/usr/lib/python3.14/site-packages"
    local stray
    for stray in tests docs rust vendor _cffi_src \
                 Cargo.toml Cargo.lock CHANGELOG.rst CONTRIBUTING.rst \
                 LICENSE LICENSE.APACHE LICENSE.BSD; do
        if [ -e "${sp}/${stray}" ]; then
            echo "removing sdist top-level entry from site-packages: ${stray}"
            rm -rf "${sp:?}/${stray}"
        fi
    done

    # Fail closed on anything left that is neither the module nor its
    # dist-info: the point is that a FUTURE upstream addition gets a decision,
    # not a silent ship and not a silent delete.
    local unexpected="" entry
    for entry in "${sp}"/*; do
        [ -e "$entry" ] || continue
        case "$(basename "$entry")" in
            cryptography|cryptography-${PKG_VERSION}.dist-info) ;;
            *) unexpected="${unexpected} $(basename "$entry")" ;;
        esac
    done
    if [ -n "$unexpected" ]; then
        echo "ERROR: unexpected top-level entries staged into site-packages:${unexpected}" >&2
        echo "       site-packages is a shared namespace — decide what owns each name before shipping it." >&2
        exit 1
    fi
    if [ ! -f "${sp}/cryptography/__init__.py" ]; then
        echo "ERROR: ${sp}/cryptography/__init__.py absent after install — the library itself did not stage." >&2
        exit 1
    fi
}
