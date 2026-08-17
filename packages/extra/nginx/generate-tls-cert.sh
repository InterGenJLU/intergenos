#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# Generate a per-machine self-signed TLS certificate for nginx.
#
# Invoked by nginx-tls-keygen.service, which nginx.service Requires= and is
# ordered After=, so the key material is created ON THE TARGET at first
# service start and is unique to each machine. Key material is NEVER generated
# at build or package-install time and therefore never ships inside an archive
# or the squashfs: a baked cert+key would be byte-identical on every install
# (a shared-private-key defect). The sample nginx.conf references
# /etc/nginx/ssl/server.{crt,key}; this script is what puts them there.
#
# Idempotent: if server.crt already exists (operator-supplied CA cert, or a
# prior run) it exits 0 without touching anything. Fail-loud otherwise — any
# openssl/chmod/chown error aborts, the unit fails, and nginx (which Requires=
# this unit) refuses to start rather than serve TLS with no or a broken cert.
set -eu

SSL_DIR=/etc/nginx/ssl
CERT="$SSL_DIR/server.crt"
KEY="$SSL_DIR/server.key"

# Operator-supplied or already-generated cert: nothing to do.
[ -f "$CERT" ] && exit 0

install -d -m 755 "$SSL_DIR"

umask 077
openssl req -x509 -newkey rsa:4096 -sha256 -nodes \
    -days 365 \
    -keyout "$KEY" \
    -out    "$CERT" \
    -subj "/CN=localhost"

chmod 644 "$CERT"
chmod 600 "$KEY"
chown root:root "$CERT" "$KEY"
