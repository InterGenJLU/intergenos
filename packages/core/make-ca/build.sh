#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# make-ca 1.16.1 — CA certificate management TOOL only
# BLFS 13.0
#
# This package installs the make-ca BINARY at /usr/sbin/make-ca so users
# who want NSS-style cert manipulation can opt in. It does NOT:
#   (a) invoke `make-ca -g` at build time to fetch Mozilla certdata.txt
#       (moving-target hole in build hermeticity); or
#   (b) enable update-pki.timer for weekly auto-fetch from Mozilla
#       (same hole, recurring).
# Both behaviors are redundant with the `ca-certificates` package, which
# deploys the hermetic in-tree-pinned curl.se cacert.pem bundle to
# /etc/ssl/certs/ca-certificates.crt and canonical symlinks per
# commit 789c7e32 "pin derived source artifacts in tree (Item #2)".
# Updates to the bundle ship via normal `pkm update` (manual, on demand
# version bump of the ca-certificates package). Item #2 follow-on
# 2026-05-24 — see also scripts/create-image.sh comment at the parallel
# call site.

configure() {
    set -e
    # Fix deprecated mktemp option
    sed '/mktemp/s/-t //' -i make-ca
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    install -vdm755 "${DESTDIR}/etc/ssl/local"

    # Profile drop-in ships as owned payload (hook-contract wave); byte/mode-
    # identical to the file the retired post_install wrote (644).
    install -dm755 "${DESTDIR}/etc/profile.d"
    cat > "${DESTDIR}/etc/profile.d/pythoncerts.sh" << "EOF"
# Begin /etc/profile.d/pythoncerts.sh
export _PIP_STANDALONE_CERT=/etc/pki/tls/certs/ca-bundle.crt
# End /etc/profile.d/pythoncerts.sh
EOF
    chmod 644 "${DESTDIR}/etc/profile.d/pythoncerts.sh"
}

