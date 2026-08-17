#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# gh 2.95.0 — GitHub's official command-line tool (MIT)
# Upstream: https://github.com/cli/cli
#
# Build profile: custom. gh's Makefile delegates `bin/gh` to
# `script/build.go` (a tiny Go build wrapper); it ultimately runs a plain
# `go build ./cmd/gh` with version/date stamped into
# internal/build.{Version,Date} via -ldflags. We drive `make bin/gh
# manpages completions` then `make install`, matching upstream's own
# packaging path exactly (no downstream patches — Fedora/Debian flags are a
# trap, build-rules §2.8). Entrypoint package is ./cmd/gh; ./cmd/gen-docs
# generates the man pages offline.
#
# Go-vendor pattern (established Go-package precedent — lego/lazygit/caddy/
# etcd at the extra tier): the build chroot is OFFLINE, so the ~hundreds of
# Go module deps are pre-vendored into a reproducible gh-2.95.0-vendor.tar.xz
# (generated host-side via `go mod vendor`, packed with --sort=name
# --owner=0 --group=0 --numeric-owner --mtime=@SOURCE_DATE_EPOCH, same
# discipline as cargo-vendor-gen.sh). configure() unpacks vendor/ + go.mod +
# go.sum at the source root; build() runs with GOFLAGS=-mod=vendor so the
# module-cache lookup short-circuits to vendor/ and never reaches
# proxy.golang.org. CGO is not required (gh is pure-Go).
#
# GOTOOLCHAIN=local: gh's go.mod declares `toolchain go1.26.4`, which would
# otherwise make `go build` try to FETCH go1.26.4 over the network. Pinning
# to local forces the in-tree go (packages/core/go = 1.26.4), which satisfies
# the `go >= 1.26.0` floor. GH_VERSION is set so script/build.go's version()
# does not fall back to `git describe` (the chroot has no .git tree).

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Unpack the pre-vendored Go module deps into the source tree
    # (wrapper dir gh-2.95.0/ holds vendor/ + go.mod + go.sum).
    if [ -f "${IGOS_SOURCES}/gh-${PKG_VERSION}-vendor.tar.xz" ]; then
        tar -xJf "${IGOS_SOURCES}/gh-${PKG_VERSION}-vendor.tar.xz" \
            --strip-components=1
    else
        echo "ERROR: gh-${PKG_VERSION}-vendor.tar.xz not found in IGOS_SOURCES"
        echo "Run: go mod vendor for gh-${PKG_VERSION} prior to build"
        exit 1
    fi
}

build() {
    set -e
    export GOTOOLCHAIN=local
    export CGO_ENABLED=0
    export GOFLAGS="-mod=vendor -buildvcs=false"
    export GH_VERSION="${PKG_VERSION}"

    # Build the binary, the man pages, and the shell completions. The
    # Makefile rules invoke script/build.go (go build ./cmd/gh) and
    # ./cmd/gen-docs — all offline against vendor/.
    make bin/gh
    make manpages
    make completions
}

check() {
    set -e
    # `go test ./...` reaches GitHub's live API in the integration suites and
    # is not chroot-friendly; the binary's identity is verified by the
    # pre-squashfs verify_paths audit + `gh --version` at first use.
    true
}

do_install() {
    set -e
    # Drive upstream's own install rule (prefix=/usr lands /usr/bin/gh,
    # /usr/share/man/man1/gh*.1, and the bash/fish/zsh completions).
    make install prefix=/usr DESTDIR="$DESTDIR"

    # Install LICENSE for runtime inspection.
    install -Dm644 LICENSE "$DESTDIR/usr/share/licenses/gh/LICENSE"
}
