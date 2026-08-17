#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# BIND Utilities 9.20.19 — DNS client tools only (dig, host, nslookup, nsupdate, rndc).
# We build the BIND tree but compile + install ONLY the client libraries and the
# client programs, NOT named or the server admin suite. Mirrors BLFS 13.0 "BIND
# Utilities" (same lib subdir order + bin set), with DESTDIR staging.

configure() {
    set -e
    # --without-jemalloc: jemalloc is extra-tier; a base package must not link a
    #   later-tier library, and the client tools don't need it. Explicit + deterministic.
    # DoH stays enabled (nghttp2 is in core); libidn2 is autodetected (present in core).
    ./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var \
        --without-jemalloc
}

build() {
    set -e
    # Client libraries in dependency order, then the client programs — NOT named.
    for d in lib/isc lib/dns lib/ns lib/isccfg lib/isccc bin/dig bin/nsupdate bin/rndc; do
        make -j${IGOS_JOBS} -C "$d"
    done
}

do_install() {
    set -e
    for d in lib/isc lib/dns lib/ns lib/isccfg lib/isccc bin/dig bin/nsupdate bin/rndc; do
        make -C "$d" DESTDIR="${DESTDIR}" install
    done
}
