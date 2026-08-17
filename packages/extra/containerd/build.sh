#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# containerd 2.2.3 — industry-standard container runtime daemon
# Upstream: https://containerd.io/
#
# Pinned to moby v29.5.2's choice (Dockerfile:145). containerd v2.2.3 + runc
# v1.3.5 + moby v29.5.2 form a bilaterally-tested trio; do not mix versions.
#
# Build approach mirrors Arch + Fedora:
#   - vendor/ ships in the tarball; -mod=vendor for chroot offline build
#   - VERSION=v2.2.3 + REVISION=2.2.3 injected via env (no .git tree)
#   - PREFIX=/usr (default is /usr/local — FHS-canonical override)
#   - patch containerd.service ExecStartPre+ExecStart to /usr/bin paths
#     (upstream ships /sbin/modprobe + /usr/local/bin/containerd; both wrong
#     for us — Arch applies the same sed, Fedora applies the ExecStart sed.)
#   - SHIM_CGO_ENABLED=1 (default; containerd-shim-runc-v2 has cgo glue)
#
# Drop containerd-stress from packaged binaries (Arch ships it, but it's a
# bench/dev tool — not part of the runtime surface). Achieved by NOT calling
# `make install` for it: we explicitly install only containerd + ctr + the
# shim binary via discrete `install -Dm755` lines below.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    # Patch upstream containerd.service: rewrite /usr/local/bin/containerd
    # → /usr/bin/containerd, AND /sbin/modprobe → /usr/bin/modprobe. Both
    # are mainstream-distro standard rewrites (Arch's prepare() does the
    # same sed). Service file lives at top level of the source tree.
    sed -i \
        -e 's|/usr/local/bin/containerd|/usr/bin/containerd|g' \
        -e 's|/sbin/modprobe|/usr/bin/modprobe|g' \
        containerd.service
}

build() {
    set -e
    export GOTOOLCHAIN=local
    export GO111MODULE=on
    export GOFLAGS="-trimpath -mod=vendor -modcacherw"
    # Inject version + revision so the Makefile's `git describe` fallback
    # doesn't fire and embed "unknown" (or fail on missing .git).
    export VERSION="v2.2.3"
    export REVISION="2.2.3"

    make binaries -j${IGOS_JOBS}
    make man      # generates man pages from docs/man/*.md via go-md2man
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        true
}

do_install() {
    set -e
    # Explicit binary installs — skip containerd-stress (dev/bench tool).
    install -Dm755 bin/containerd                 "$DESTDIR/usr/bin/containerd"
    install -Dm755 bin/ctr                        "$DESTDIR/usr/bin/ctr"
    install -Dm755 bin/containerd-shim-runc-v2    "$DESTDIR/usr/bin/containerd-shim-runc-v2"

    # systemd unit (patched by configure() above)
    install -Dm644 containerd.service \
        "$DESTDIR/usr/lib/systemd/system/containerd.service"

    # Generate default config and ship as /etc/containerd/config.toml.
    # Equivalent to Fedora's Source3 file, but produced at build time from
    # the binary we just built so the config matches the daemon version's
    # schema exactly (vs. shipping a hand-curated file that can rot).
    install -d -m 755 "$DESTDIR/etc/containerd"
    "$DESTDIR/usr/bin/containerd" config default > "$DESTDIR/etc/containerd/config.toml"
    chmod 644 "$DESTDIR/etc/containerd/config.toml"

    # Man pages
    install -d -m 755 "$DESTDIR/usr/share/man/man5"
    install -d -m 755 "$DESTDIR/usr/share/man/man8"
    install -Dm644 man/containerd.8                  "$DESTDIR/usr/share/man/man8/containerd.8"
    install -Dm644 man/containerd-config.8           "$DESTDIR/usr/share/man/man8/containerd-config.8"
    install -Dm644 man/ctr.8                         "$DESTDIR/usr/share/man/man8/ctr.8"
    install -Dm644 man/containerd-config.toml.5      "$DESTDIR/usr/share/man/man5/containerd-config.toml.5"

    # Bash completion for ctr
    install -Dm644 contrib/autocomplete/ctr \
        "$DESTDIR/usr/share/bash-completion/completions/ctr"

    # Note: containerd.service ships DISABLED. The security-only-alignment principle is "don't auto-
    # enable a root-privileged container daemon." Operators who actually want
    # docker functional run `systemctl enable --now containerd docker.socket`
    # explicitly. Mirror's purpose is opt-in, not surprise-running daemons.
}
