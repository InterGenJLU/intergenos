#!/bin/bash
# ca-certificates 2026.04.30 — Mozilla CA root certificate bundle
#
# Source: curl.se's cacert.pem (extracted from Mozilla NSS's certdata.txt).
# License: MPL-2.0 (same as Mozilla's NSS source).
#
# v1.0-dev1 scope: ship the pre-compiled bundle at the canonical paths that
# OpenSSL, GnuTLS, curl, wget, Python, and Go consult. Skip the Debian-style
# /usr/share/ca-certificates/<vendor>/*.crt split + update-ca-certificates
# manifest mechanism — that's K-tracker'd for v1.x when we want per-cert
# disable + per-system trust additions to be a first-class workflow.
#
# Install layout (matches the standard LFS/BLFS/Arch placement):
#   /etc/ssl/certs/ca-certificates.crt        - bundle (Debian + Arch
#                                               default; OpenSSL on most
#                                               builds; the path Wave B.1's
#                                               build-squashfs.sh check
#                                               looks for)
#   /etc/ssl/cert.pem                         - symlink (BSD-flavored apps,
#                                               libressl, some Go-based)
#   /etc/pki/tls/certs/ca-bundle.crt          - symlink (RHEL/Fedora layout;
#                                               our profile.d/pythoncerts.sh
#                                               exports _PIP_STANDALONE_CERT
#                                               pointing here)
#   /etc/pki/ca-trust/source/anchors/         - empty dir (v1.x per-cert
#                                               anchor drop-in point)
#
# Provenance audit-trail: cacert.pem in the source tarball is the
# curl.se cacert snapshot as of 2026-04-30, downloaded directly from
# https://curl.se/ca/cacert.pem (always-latest, no dated mirror at fetch
# time). sha256 of the inner pem: 86a1f3366afac7c6f8ae9f3c779ac221129328c43f0ab2b8817eb2f362a5025c

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/etc/ssl/certs"
    install -dm755 "${DESTDIR}/etc/pki/tls/certs"
    install -dm755 "${DESTDIR}/etc/pki/ca-trust/source/anchors"

    install -m 644 cacert.pem "${DESTDIR}/etc/ssl/certs/ca-certificates.crt"

    ln -sf /etc/ssl/certs/ca-certificates.crt "${DESTDIR}/etc/ssl/cert.pem"
    ln -sf /etc/ssl/certs/ca-certificates.crt "${DESTDIR}/etc/pki/tls/certs/ca-bundle.crt"
}

post_install() {
    set -e
    :
}
