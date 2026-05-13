#!/bin/bash
# lego 5.0.2 — Go-based ACME client (Let's Encrypt + RFC 8555)
#
# Wave W1 cert-companion landing per the certbot→lego rotation swap. The
# certbot slot in the web-server-wave was retired after a halt-and-propose
# audit found (a) certbot 5.6.0 published inside the 2026-05-11/12 PyPI
# attack window, (b) 9+ transitive Python deps not in tree, and (c) no
# Python-vendor-gen pipeline analogous to cargo-vendor or go-vendor. lego
# fits the established Go-vendor pattern + ships as a single static binary,
# making it a structurally cleaner v1.0 ACME client.
#
# Why this version (5.0.2, current latest stable):
#   * Released 2026-05-12T21:33:17Z per github.com/go-acme/lego/releases.
#     Note timing: 2026-05-12 is the second day of the PyPI attack window
#     BUT lego is Go, not Python; Go modules fetch through
#     proxy.golang.org (and via the vendored copy here, bypassing the
#     network entirely at build time). The PyPI attack window is
#     unrelated to lego's release-channel integrity.
#   * Single SHA-256 anchor on the upstream GitHub archive pinned in
#     package.yml after local sha256sum verification.
#
# License: MIT. The LICENSE file at the tarball root carries the standard
# MIT notice ("Copyright (c) 2017-2024 Ludovic Fernandez" + "Copyright
# (c) 2015-2017 Sebastian Erhart" — the latter is the original `xenolf/
# acme` author from when lego was the canonical Let's Encrypt Go client).
#
# Build profile: custom (cd cmd/lego && go build). lego's main package
# is at cmd/lego/main.go; the top-level repo is a Go module with the
# library code under acme/ + challenge/ + providers/ subtrees.
#
# Go-vendor pattern (the established Wave W1 + earlier Go-package
# precedent — etcd/caddy at 039a05c2/898aea90):
#   1. configure() extracts the lego-5.0.2-vendor.tar.xz reproducible
#      tarball into the source tree, placing vendor/ + go.mod + go.sum
#      at the project root.
#   2. build() invokes `go build` with GOFLAGS="-mod=vendor" so the
#      module-cache lookup short-circuits to vendor/ instead of
#      reaching proxy.golang.org.
#   3. CGO_ENABLED=0 produces a fully-static binary (no runtime libc
#      dependency for the lego binary itself; openssl-via-Go-stdlib
#      satisfies the ACME crypto path).
#   4. Reproducibility flags: -trimpath strips source paths from binary;
#      -ldflags '-s -w' strips debug + symbol info; -buildvcs=false
#      omits VCS-revision-baked-into-binary.
#
# Source-tarball provenance: GitHub auto-archive at
# https://github.com/go-acme/lego/archive/refs/tags/v5.0.2.tar.gz
# (1,246,315 bytes; no upstream-uploaded release-asset for source — the
# pre-built binaries on the release page are Linux/macOS/Windows
# distributions, not source).
#
# Vendor-tarball provenance: built locally via `go mod vendor` against
# lego 5.0.2 source tree using the in-tree-bootstrapped go1.26.2
# toolchain (matches packages/core/go/package.yml anchor). 13 top-level
# direct module deps; 92MB unpacked; 7.4MB after `xz -T 1` compression.
# Built reproducibly with --sort=name + --owner=0 + --group=0 +
# --numeric-owner + --mtime=@${SOURCE_DATE_EPOCH}; same flags as the
# cargo-vendor-gen.sh canonical recipe.
#
# Vendor tarball sha256 (full value pinned in package.yml; elided here
# to 8-char prefix per the public-content audit convention): fc83c2c0...
#
# This sha256 is pinned in package.yml as the second source entry so
# the build mirror's source-staging machinery picks up the tarball
# alongside the source archive.
#
# Security-only-alignment posture: lego is a one-shot CLI tool. No
# daemon process. No setuid surface. No privileged entry points. The
# only network surface lego itself opens is the outbound ACME protocol
# session to Let's Encrypt (or operator-specified CA). At certificate-
# renewal time, lego is typically invoked via a systemd timer or cron
# job; the systemd-unit shape is left to the operator (sample at
# /usr/share/lego/sample-renew.sh as a starting template).
#
# Compatibility with the Wave W1 web servers (apache-httpd / nginx /
# caddy / lighttpd / haproxy):
#   - HTTP-01 challenge: lego --http --http.webroot <dir> ...
#                        works against ANY of the five (apache, nginx,
#                        caddy, lighttpd, haproxy) when configured with
#                        a static-file webroot. Caddy has its own
#                        built-in ACME but lego is the deliberate
#                        operator-driven alternative when the apache /
#                        nginx side wants centralized certs.
#   - DNS-01 challenge: lego --dns <provider> --domains ...
#                       requires provider credentials. lego ships
#                       built-in support for ~70 DNS providers (the
#                       providers/ subtree).
#   - TLS-ALPN-01 challenge: lego --tls --tls.port :443 ...
#                            (binds directly to :443; the operator
#                            shuts down the existing web server during
#                            the challenge window).
#
# v1.x followup captured fleet-side: certbot will land later when (a)
# the PyPI supply-chain situation is provably-clean AND (b) a Python-
# vendor-gen pipeline analogous to cargo-vendor-gen.sh + go-mod-vendor
# is established in tree. Both certbot and lego will coexist long-term.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Extract the vendor tarball into the source tree. The tarball has
    # a single top-level wrapper dir `lego-5.0.2/` containing vendor/
    # + go.mod + go.sum; --strip-components=1 unwraps it into cwd.
    tar xJf "${IGOS_SOURCES}/lego-${PKG_VERSION}-vendor.tar.xz"             \
        --strip-components=1
}

build() {
    set -e
    # lego 5.0.2 has main.go at the repo root, NOT under cmd/lego/. The
    # cmd/ subdir contains the cobra subcommand handlers (cmd.go etc.) but
    # the entrypoint binary is built from the root module.
    #
    # Output name lego.bin (NOT plain `lego`) — the source tree ALSO has a
    # `lego/` library subdirectory at cwd, and `go build -o lego` would
    # interpret the existing dir as a target location and stash the binary
    # at lego/lego instead of cwd/lego.
    CGO_ENABLED=0 GOFLAGS="-mod=vendor" go build                            \
        -trimpath                                                           \
        -ldflags '-s -w'                                                    \
        -buildvcs=false                                                     \
        -o lego.bin                                                         \
        .
}

do_install() {
    set -e
    # Install the binary (renamed from lego.bin → lego on install per
    # the build-output naming workaround above)
    install -Dm755 lego.bin "$DESTDIR/usr/bin/lego"

    # Install the sample renewal helper script (documentation +
    # starting template; operator copies + customizes).
    install -Dm755 "$BUILD_DIR/sample-renew.sh"                             \
        "$DESTDIR/usr/share/lego/sample-renew.sh"

    # Install LICENSE for runtime inspection.
    install -Dm644 LICENSE "$DESTDIR/usr/share/licenses/lego/LICENSE"

    # Create the canonical state dir lego uses for cert + account
    # storage (--path default is `.lego`; we override that via the
    # sample-renew.sh helper to use a system-wide location).
    install -d -m 750 "$DESTDIR/var/lib/lego"
}

check() {
    set -e
    # lego's `go test ./...` runs ~2000 unit tests + integration tests
    # that touch network ACME endpoints (Let's Encrypt staging). Not
    # chroot-friendly. The shipped binary's verification happens at
    # first-use by the operator (lego --version + a dry-run challenge).
    return 0
}
