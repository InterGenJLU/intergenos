#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# rsyslog 8.2608.0 — system log processor with the RELP acknowledged transport.
#
# Verified against the pinned tarball's configure.ac:
#   - autotools with a generated ./configure (upstream release tarball).
#   - Required always: libestr >= 0.1.9 (:85), libfastjson >= 0.99.9 (:87).
#   - --enable-relp (default no, :2080) needs relp >= 1.2.14 (:2090) → librelp.
#   - --enable-uuid (default auto, :1232) needs uuid (:1242) → util-linux-core.
#   - --enable-libsystemd (default auto, :789) + --enable-imjournal (default no, :756)
#     + --enable-omjournal (:2727) need libsystemd (:768/:800) → systemd.
#   - --enable-openssl (:1384) needs openssl (:1394); --enable-libgcrypt (:1504).
#   - --enable-imfile (:2302), --enable-imptcp (:2529), --enable-impstats (:2543),
#     --enable-mmjsonparse (:1840) have no dependency beyond the always-required pair.
#   - GnuTLS stream driver (--enable-gnutls, :1446) is NOT enabled: the RELP transport
#     carries its TLS capability in librelp, and the design's TLS fallback names
#     RELP/TLS, not the plain-TCP driver. liblognorm/liblogging-stdlog (:1769/:2146)
#     are not packaged and their modules are not enabled — no feature is disabled to
#     dodge a dependency; these are simply outside the design.
#   - Upstream ships no systemd unit; platform/redhat/rsyslog.conf is a distro
#     example, not installed. The unit and the default configuration are tracked
#     files in this recipe (the nftables precedent).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    ./configure                              \
        --prefix=/usr                        \
        --sysconfdir=/etc                    \
        --localstatedir=/var                 \
        --libexecdir=/usr/libexec            \
        --mandir=/usr/share/man              \
        --disable-static                     \
        --enable-relp                        \
        --enable-imfile                      \
        --enable-imptcp                      \
        --enable-impstats                    \
        --enable-mmjsonparse                 \
        --enable-uuid                        \
        --enable-libsystemd                  \
        --enable-imjournal                   \
        --enable-omjournal                   \
        --enable-openssl                     \
        --enable-libgcrypt                   \
        --disable-gnutls                     \
        --disable-generate-man-pages
}

build() {
    set -e
    make -j"${IGOS_JOBS:-$(nproc)}"
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" install

    # Default configuration: local sockets + journal import, classic files under
    # /var/log, a working directory for queues, and an include directory for the
    # platform's own rules. No network input or output is loaded by default.
    install -Dm644 "$BUILD_DIR/rsyslog.conf" "$DESTDIR/etc/rsyslog.conf"
    install -dm755 "$DESTDIR/etc/rsyslog.d"
    install -dm700 "$DESTDIR/var/spool/rsyslog"

    # The unit is a tracked file (upstream ships none). It is NOT preset-enabled:
    # the catch-all 99-intergenos-default-disable.preset leaves it disabled, and
    # only a deployment record enables a log receiver on a given host.
    install -Dm644 "$BUILD_DIR/rsyslog.service" \
                   "$DESTDIR/usr/lib/systemd/system/rsyslog.service"
}
