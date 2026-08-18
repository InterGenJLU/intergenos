#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# docker-buildx 0.36.1 — the Docker CLI plugin that exposes BuildKit's build
# features (multi-platform builds, build cache export/import, named contexts).
#
# Build facts verified against the pinned tarball:
#   - Go, and vendor/ IS present with a vendor/modules.txt (90 MB, 112
#     top-level module directories), so the build resolves every dependency
#     from the tarball and never reaches the network. This is what makes the
#     package buildable inside the chroot at all — see the delivery note about
#     docker-compose, whose tag archive ships no vendor tree.
#   - The binary is a CLI plugin: docker discovers it by filename in
#     /usr/libexec/docker/cli-plugins, which the docker package already creates.
#     It is deliberately NOT installed into /usr/bin — a plugin binary run
#     directly prints a plugin-protocol error, so putting it on PATH would only
#     create a confusing command.
#
# The version and revision are injected rather than derived: the Makefile's
# version logic falls back to `git describe`, and the release tarball has no
# .git tree, so without these the binary would report an empty or "unknown"
# version. That is the same class the docker/runc/containerd recipes in this
# tree already handle the same way, and `docker buildx version` is the check
# that the injection worked.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    true  # nothing to configure; the orchestrator has already extracted here
}

build() {
    set -e
    export GOTOOLCHAIN=local
    export CGO_ENABLED=0
    export GO111MODULE=on
    export GOFLAGS="-trimpath -mod=vendor -modcacherw"

    go build \
        -ldflags "-X github.com/docker/buildx/version.Version=v0.36.1 \
                  -X github.com/docker/buildx/version.Revision=v0.36.1 \
                  -X github.com/docker/buildx/version.Package=github.com/docker/buildx" \
        -o bin/docker-buildx ./cmd/buildx
}

do_install() {
    set -e
    install -Dm755 bin/docker-buildx \
        "${DESTDIR}/usr/libexec/docker/cli-plugins/docker-buildx"
}
