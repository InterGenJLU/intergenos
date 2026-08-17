#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# Generate a per-machine self-signed TLS certificate for Apache httpd.
#
# Invoked by httpd-tls-keygen.service, which httpd.service Requires= and is
# ordered After=, so the key material is created ON THE TARGET at first
# service start and is unique to each machine. Key material is NEVER
# generated at build or package-install time and therefore never ships
# inside an archive or the squashfs: a baked cert+key would be byte-identical
# on every install (a shared-private-key defect). The default TLS config
# references /etc/httpd/ssl/server.{pem,key}; this script is what puts them
# there.
#
# Idempotent: if server.pem already exists (operator-supplied CA cert, or a
# prior run) it exits 0 without touching anything. Fail-loud otherwise — any
# openssl/chmod/chown error aborts, the unit fails, and httpd (which Requires=
# this unit) refuses to start rather than serve TLS with no or a broken cert.
set -eu

SSL_DIR=/etc/httpd/ssl
CERT="$SSL_DIR/server.pem"
KEY="$SSL_DIR/server.key"

# Operator-supplied or already-generated cert: nothing to do.
[ -f "$CERT" ] && exit 0

install -d -m 750 "$SSL_DIR"

umask 077
openssl req -x509 -nodes -newkey rsa:4096 \
    -keyout "$KEY" \
    -out    "$CERT" \
    -days 365 \
    -subj "/CN=localhost/O=InterGenOS/OU=apache-httpd default"

chmod 600 "$KEY"
chmod 644 "$CERT"
chown root:apache "$KEY" "$CERT"
