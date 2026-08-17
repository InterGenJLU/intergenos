#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
    # p11-kit trust anchor dir. Our nss package points NSS's roots module at
    # p11-kit (libnssckbi.so -> p11-kit-trust.so) and our p11-kit is built with
    # trust_paths=/etc/pki/anchors. p11-kit-trust exposes a cert as a CA anchor
    # ONLY if it is in OpenSSL "TRUSTED CERTIFICATE" format here; plain certs are
    # ignored. Populated in the split loop below. Without it, NSS/Firefox get
    # ZERO roots and reject every HTTPS site ("Did Not Connect: Potential
    # Security Issue") while OpenSSL/curl work off the bundle above — the gap
    # that broke the browser on a fresh install (.241/.227, 2026-06-10).
    install -dm755 "${DESTDIR}/etc/pki/anchors"

    install -m 644 cacert.pem "${DESTDIR}/etc/ssl/certs/ca-certificates.crt"

    ln -sf /etc/ssl/certs/ca-certificates.crt "${DESTDIR}/etc/ssl/cert.pem"
    ln -sf /etc/ssl/certs/ca-certificates.crt "${DESTDIR}/etc/pki/tls/certs/ca-bundle.crt"

    # Split the cacert.pem file into per-cert PEM files and rehash so
    # callers that consult /etc/ssl/certs/ as a hashed directory (curl
    # built --with-ca-path, openssl s_client default, GnuTLS, Go's
    # crypto/x509 default Unix path) find the same trust anchors as
    # callers that consult the bundle file directly. Without this
    # step, curl + openssl-cli + Go default-context HTTPS verifies
    # fail on a fresh install — the 2026-05-25 live-ISO regression
    # where vscode / chrome / edge all silently
    # broke at install time because curl couldn't verify Microsoft
    # or Google apt pool TLS chains.
    #
    # Algorithm: scan the PEM file, emit each cert separately, derive
    # each cert's subject hash via `openssl x509 -hash`, install each
    # cert at /etc/ssl/certs/<hash>.0 (or .1, .2... if the hash
    # collides, matching `c_rehash` / `openssl rehash` semantics).
    # Done in pure shell so we don't need the openssl c_rehash perl
    # helper at build time.
    awk 'BEGIN { n=0 }
         /-----BEGIN CERTIFICATE-----/ { capture=1; cert="" }
         capture { cert = cert $0 "\n" }
         /-----END CERTIFICATE-----/ {
             capture=0; n++;
             printf "%s", cert > "/tmp/igos-cacert-" n ".pem";
             close("/tmp/igos-cacert-" n ".pem");
         }
         END { print n > "/tmp/igos-cacert-count" }
    ' cacert.pem

    count=$(cat /tmp/igos-cacert-count)
    seen=""
    i=1
    while [ "$i" -le "$count" ]; do
        src="/tmp/igos-cacert-${i}.pem"
        hash=$(openssl x509 -in "$src" -hash -noout 2>/dev/null)
        if [ -n "$hash" ]; then
            # Collision-safe suffix: find lowest unused integer.
            suffix=0
            while echo "$seen" | grep -qE "(^| )${hash}.${suffix}( |$)"; do
                suffix=$((suffix + 1))
            done
            seen="$seen ${hash}.${suffix}"
            install -m644 "$src" "${DESTDIR}/etc/ssl/certs/${hash}.${suffix}"

            # Emit the same root in OpenSSL "TRUSTED CERTIFICATE" format into the
            # p11-kit anchor dir so NSS/Firefox (libnssckbi -> p11-kit-trust ->
            # /etc/pki/anchors) actually sees a root set. -addtrust serverAuth is
            # what p11-kit-trust requires to treat the cert as a CA anchor; the
            # alias is just the friendly name shown by `trust list`. curl's
            # cacert.pem is already the serverAuth-trusted Mozilla set, so
            # serverAuth is the correct purpose for every cert here.
            cn=$(openssl x509 -in "$src" -noout -subject 2>/dev/null \
                 | sed -e 's/.*CN *= *//' -e 's/.*O *= *//' | head -c 64)
            openssl x509 -in "$src" -addtrust serverAuth -setalias "${cn:-anchor}" \
                -out "${DESTDIR}/etc/pki/anchors/${hash}.${suffix}.pem" 2>/dev/null || true
        fi
        rm -f "$src"
        i=$((i + 1))
    done
    rm -f /tmp/igos-cacert-count

    # Sanity floor: a populated anchor dir is what makes the browser work. If
    # the conversion produced nothing the build is shipping a broken trust store.
    anchors=$(ls "${DESTDIR}/etc/pki/anchors"/*.pem 2>/dev/null | wc -l)
    if [ "$anchors" -lt 100 ]; then
        echo "FATAL: only ${anchors} p11-kit trust anchors emitted (<100) — NSS/Firefox would have no roots" >&2
        exit 1
    fi
    echo "ca-certificates: emitted ${anchors} p11-kit trust anchors into /etc/pki/anchors"
}

post_install() {
    set -e
    :
}
