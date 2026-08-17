#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# tailscale 1.98.5 — WireGuard-based mesh VPN (BSD-3-Clause)
# Upstream: https://github.com/tailscale/tailscale
#
# Build profile: custom. ONE Go module (tailscale.com) produces TWO binaries:
#   * tailscale   (./cmd/tailscale)  — the user CLI
#   * tailscaled  (./cmd/tailscaled) — the long-running node-agent DAEMON
#
# We do NOT use upstream's Makefile: its targets call `./tool/go` (which
# downloads its own pinned toolchain over the network) and `go install` +
# depaware, none of which fit the offline chroot. Instead we replicate
# build_dist.sh's essential step — a plain `go build` with the version
# stamped into tailscale.com/version via -ldflags (longStamp/shortStamp).
# (Fedora/Arch/Debian recipes confirm plain `go build ./cmd/...` is the
# supported distro build; their downstream flags are a trap — build-rules §2.8.)
#
# Go-vendor pattern (established extra-tier Go precedent — lego/lazygit/caddy/
# etcd): the chroot is OFFLINE, so deps are pre-vendored into a reproducible
# tailscale-1.98.5-vendor.tar.xz (host-side `go mod vendor`, packed with
# --sort=name --owner=0 --group=0 --numeric-owner --mtime=@SOURCE_DATE_EPOCH;
# same discipline as cargo-vendor-gen.sh). GOFLAGS=-mod=vendor short-circuits
# the module-cache lookup to vendor/ — never reaches proxy.golang.org.
#
# GOTOOLCHAIN=local: tailscale's go.mod declares `go 1.26.3`; pinning to local
# forces the in-tree go (packages/core/go = 1.26.4, bumped from 1.26.2 expressly
# to clear this floor — 1.26.4 >= 1.26.3, satisfied). CGO_ENABLED=0 →
# fully-static binaries (no runtime libc dep).
#
# SERVICE PACKAGE (08-adding-packages "Service packages"): tailscaled is a
# daemon. We ship upstream's own systemd unit (ExecStart=/usr/sbin/tailscaled)
# as a TRACKED file plus the /etc/default/tailscaled env file it references.
# We deliberately ship NO 90-tailscaled.preset: tailscaled is an
# UNCONDITIONAL network daemon (joins a mesh VPN, needs operator auth), so per
# the doc's "only enable by default what should run by default" it stays
# DISABLED (the 99-* catch-all wins) until the user runs `systemctl enable
# --now tailscaled`. Same posture as caddy/etcd/influxdb (all ship a unit, no
# preset). Security-only-alignment: default-deny for a network-facing daemon.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Unpack the pre-vendored Go module deps (wrapper dir
    # tailscale-1.98.5/ holds vendor/ + go.mod + go.sum).
    if [ -f "${IGOS_SOURCES}/tailscale-${PKG_VERSION}-vendor.tar.xz" ]; then
        tar -xJf "${IGOS_SOURCES}/tailscale-${PKG_VERSION}-vendor.tar.xz" \
            --strip-components=1
    else
        echo "ERROR: tailscale-${PKG_VERSION}-vendor.tar.xz not found in IGOS_SOURCES"
        echo "Run: go mod vendor for tailscale-${PKG_VERSION} prior to build"
        exit 1
    fi
}

build() {
    set -e
    export GOTOOLCHAIN=local
    export CGO_ENABLED=0
    export GOFLAGS="-mod=vendor -buildvcs=false"

    # Stamp the version the way build_dist.sh does. No git tree in the
    # chroot, so feed the version literals directly.
    local ver="${PKG_VERSION}"
    local ldflags="-X tailscale.com/version.longStamp=${ver} -X tailscale.com/version.shortStamp=${ver} -s -w"

    go build -trimpath -ldflags "${ldflags}" -o tailscale  ./cmd/tailscale
    go build -trimpath -ldflags "${ldflags}" -o tailscaled ./cmd/tailscaled
}

check() {
    set -e
    # `go test ./...` exercises network/namespace paths not available in the
    # build chroot; binary identity is proven by the verify_paths audit +
    # `tailscale version` at first use.
    true
}

do_install() {
    set -e
    # CLI → /usr/bin, daemon → /usr/sbin (upstream-canonical; the shipped
    # unit's ExecStart points at /usr/sbin/tailscaled).
    install -Dm755 tailscale  "$DESTDIR/usr/bin/tailscale"
    install -Dm755 tailscaled "$DESTDIR/usr/sbin/tailscaled"

    # Daemon env file referenced by EnvironmentFile= in the unit.
    install -Dm644 "$BUILD_DIR/tailscaled.defaults" \
        "$DESTDIR/etc/default/tailscaled"

    # systemd unit (tracked copy of upstream's; NO preset — see header).
    install -Dm644 "$BUILD_DIR/tailscaled.service" \
        "$DESTDIR/usr/lib/systemd/system/tailscaled.service"

    # State dir the daemon writes its node key/state into.
    install -d -m 700 "$DESTDIR/var/lib/tailscale"

    # LICENSE for runtime inspection.
    install -Dm644 LICENSE "$DESTDIR/usr/share/licenses/tailscale/LICENSE"
}
