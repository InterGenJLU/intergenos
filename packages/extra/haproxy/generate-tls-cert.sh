#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# Generate a per-machine self-signed TLS certificate for HAProxy.
#
# Invoked by haproxy-tls-keygen.service, which haproxy.service Requires= and
# is ordered After=, so the key material is created ON THE TARGET at first
# service start and is unique to each machine. Key material is NEVER generated
# at build or package-install time and therefore never ships inside an archive
# or the squashfs: a baked cert+key would be byte-identical on every install
# (a shared-private-key defect). HAProxy expects the cert and key concatenated
# into a single PEM; the sample haproxy.cfg references /etc/haproxy/ssl/
# server.pem, which this script is what puts there.
#
# Idempotent: if server.pem already exists (operator-supplied cert, or a prior
# run) it exits 0 without touching anything. Fail-loud otherwise — any
# openssl/cat/chmod/chown error aborts, the unit fails, and haproxy (which
# Requires= this unit) refuses to start rather than serve TLS with no or a
# broken cert.
set -eu

SSL_DIR=/etc/haproxy/ssl
CERT_PEM="$SSL_DIR/server.pem"

# Operator-supplied or already-generated cert: nothing to do.
[ -f "$CERT_PEM" ] && exit 0

install -d -m 755 "$SSL_DIR"

TMP_KEY=$(mktemp)
TMP_CRT=$(mktemp)
trap 'rm -f "$TMP_KEY" "$TMP_CRT"' EXIT

umask 077
openssl req -x509 -newkey rsa:4096 -sha256 -nodes \
    -days 365 \
    -keyout "$TMP_KEY" \
    -out    "$TMP_CRT" \
    -subj "/CN=localhost"

cat "$TMP_CRT" "$TMP_KEY" > "$CERT_PEM"
chmod 600 "$CERT_PEM"
chown root:root "$CERT_PEM"
