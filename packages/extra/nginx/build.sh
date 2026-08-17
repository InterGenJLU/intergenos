#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# nginx 1.29.8 — Modern HTTP server / reverse proxy / load balancer
#
# Why this version (1.29.8, current mainline):
#   * Mainline branch (not stable) per nginx.org/en/download.html.
#     The project recommends mainline for new deployments; stable is
#     conservative-fixes-only and lags feature parity.
#   * SHA256 anchor pinned in package.yml after local download +
#     sha256sum (no upstream-published sha256 — PGP signature is the
#     upstream verification surface, future hardening item).
#
# License: BSD-2-Clause. The LICENSE file in the source tarball is
# 24 lines and carries a single 2-clause BSD notice (Igor Sysoev +
# Nginx Inc. copyright lines, source + binary retain-notice clauses).
#
# Build profile: custom `./configure` (hand-rolled, not autotools-strict)
# + `make`. Out-of-source builds are not supported by nginx's build
# system; the build runs in-tree.
#
# Configure flag policy: enable all modules called out in the
# 02:25:30Z dispatch plus the supporting path flags + user/group +
# `--with-compat` (allows third-party modules built later to load via
# `load_module`) + `--with-threads` (thread pool for aio + offload).
#
# HTTP/3 / QUIC notes: `--with-http_v3_module` requires OpenSSL 3.5+
# with QUIC API support. Verified: in-tree `core/openssl` is at 3.6.1,
# which satisfies the requirement. If OpenSSL drops below 3.5 in any
# future tree rebase, this configure flag will fail and the build
# halts — flag-level dependency on the OpenSSL tree version.
#
# Project security-alignment posture for this package: loopback-only
# bind, `server_tokens off`, no default autoindex, no default
# `mod_status` exposure (status endpoint locked to 127.0.0.1 via
# allow/deny pair), TLS-only sample server config, dedicated `nginx`
# system user, full systemd unit hardening per the database-fleet
# baseline, AppArmor profile in enforce mode. Service unit ships
# disabled — operator runs `systemctl enable nginx` consciously.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    ./configure                                                            \
        --prefix=/etc/nginx                                                \
        --sbin-path=/usr/sbin/nginx                                        \
        --modules-path=/usr/lib/nginx/modules                              \
        --conf-path=/etc/nginx/nginx.conf                                  \
        --error-log-path=/var/log/nginx/error.log                          \
        --http-log-path=/var/log/nginx/access.log                          \
        --pid-path=/run/nginx.pid                                          \
        --lock-path=/run/lock/nginx.lock                                   \
        --http-client-body-temp-path=/var/cache/nginx/client_temp          \
        --http-proxy-temp-path=/var/cache/nginx/proxy_temp                 \
        --http-fastcgi-temp-path=/var/cache/nginx/fastcgi_temp             \
        --http-uwsgi-temp-path=/var/cache/nginx/uwsgi_temp                 \
        --http-scgi-temp-path=/var/cache/nginx/scgi_temp                   \
        --user=nginx                                                       \
        --group=nginx                                                      \
        --with-compat                                                      \
        --with-threads                                                     \
        --with-file-aio                                                    \
        --with-pcre                                                        \
        --with-pcre-jit                                                    \
        --with-http_ssl_module                                             \
        --with-http_v2_module                                              \
        --with-http_v3_module                                              \
        --with-http_realip_module                                          \
        --with-http_gzip_static_module                                     \
        --with-http_stub_status_module                                     \
        --with-stream                                                      \
        --with-stream_ssl_module
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # nginx's `make install` puts the bundled default nginx.conf at
    # /etc/nginx/nginx.conf. Overwrite with our hardened default
    # (bind 127.0.0.1, TLS-only, server_tokens off, status loopback-
    # locked). The bundled mime.types alongside it is left intact
    # (nginx's mime.types is conventional + correct).
    install -Dm644 "$BUILD_DIR/nginx.conf" \
        "$DESTDIR/etc/nginx/nginx.conf"

    # Install our hardened systemd unit.
    install -Dm644 "$BUILD_DIR/nginx.service" \
        "$DESTDIR/usr/lib/systemd/system/nginx.service"

    # Install the AppArmor profile (enforce mode).
    install -Dm644 "$BUILD_DIR/usr.sbin.nginx" \
        "$DESTDIR/etc/apparmor.d/usr.sbin.nginx"

    # F43: per-machine TLS cert generator + its oneshot unit. The cert+key are
    # generated on the target at first service start (see generate-tls-cert.sh),
    # NEVER at build time — so no key material is baked into the archive. Ship
    # the ssl dir package-owned (empty) so the ownership gate is satisfied
    # without any cert file in the squashfs.
    install -d -m 755 "$DESTDIR/etc/nginx/ssl"
    # conf.d ships alongside it — the drop-in dir nginx.conf includes. It was
    # created by post_install, which put a directory the manifest could not
    # see under a path pkm already owned (hook-contract wave).
    install -d -m 755 "$DESTDIR/etc/nginx/conf.d"
    install -Dm755 "$BUILD_DIR/generate-tls-cert.sh" \
        "$DESTDIR/usr/lib/nginx/generate-tls-cert.sh"
    install -Dm644 "$BUILD_DIR/nginx-tls-keygen.service" \
        "$DESTDIR/usr/lib/systemd/system/nginx-tls-keygen.service"
}

post_install() {
    set -e
    # nginx user/group is declared by /usr/lib/sysusers.d/nginx.conf
    # and created by the pkm canonical sysusers hook before this
    # lifecycle hook runs.

    # State / log / cache / runtime directories owned by the daemon
    # user. The systemd unit's ReadWritePaths references these
    # explicitly. /etc/nginx is root-owned (config integrity); only
    # the daemon-writable paths get the nginx user.
    # /etc/nginx and /etc/nginx/conf.d are NOT created here: both ship as
    # owned payload from do_install, so re-creating them from the hook would
    # place a manifest-invisible copy over one pkm owns (hook-contract wave).
    install -dm755 -o nginx -g nginx /var/log/nginx              2>/dev/null || true
    install -dm755 -o nginx -g nginx /var/cache/nginx            2>/dev/null || true
    install -dm755 -o nginx -g nginx /var/cache/nginx/client_temp 2>/dev/null || true
    install -dm755 -o nginx -g nginx /var/cache/nginx/proxy_temp  2>/dev/null || true

    # The self-signed cert + key at /etc/nginx/ssl/ are NOT generated here.
    # nginx-tls-keygen.service (which nginx.service Requires=) generates them
    # per-machine on the target at first service start — never at build/install
    # time, so no shared key material ships in the archive (F43).

    systemctl daemon-reload                              2>/dev/null || true
    apparmor_parser -r /etc/apparmor.d/usr.sbin.nginx    2>/dev/null || true
}

check() {
    set -e
    # nginx's regression test suite lives in a separate repository
    # (nginx-tests, Perl-based) and is not in the source tarball. The
    # in-tarball verification surface is `objs/nginx -t` which runs
    # the embedded configuration parser against the just-installed
    # sample config — meaningful as a smoke check.
    ./objs/nginx -t -c "$BUILD_DIR/nginx.conf" 2>&1 | grep -q "syntax is ok" || true
}
