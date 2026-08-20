#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# skopeo 1.24.0 — moves and inspects container images without a daemon: copy
# between registries and local stores, read a manifest without pulling layers,
# and sign or verify an image's signature.
#
# Build facts verified against the pinned tarball:
#   - Go with vendor/ PRESENT (49 MB, vendor/modules.txt included) — builds
#     offline inside the chroot.
#   - Build tags are probe-derived exactly as in buildah
#     (hack/btrfs_installed_tag.sh, hack/libsubid_tag.sh, hack/sqlite_tag.sh —
#     Makefile:93-97), so every probed library is a declared build dependency
#     here to make the outcome deterministic.
#   - CGO stays ENABLED. The Makefile's DISABLE_CGO=1 path force-overrides
#     BUILDTAGS to `exclude_graphdriver_btrfs containers_image_openpgp`
#     (Makefile:100), which swaps the real GPGME signature backend for a pure-Go
#     one. That would change which implementation validates an image signature
#     and would stop skopeo using the system's GnuPG stack, so it is not taken:
#     gpgme is a core package here and linking it keeps signature verification
#     on the same crypto stack the rest of the system uses.
#
# ⚠️ INSTALL TARGET CHOICE, and it is the load-bearing line in this recipe.
# The top-level `install` target also writes ${CONTAINERSCONFDIR}/policy.json
# and ${REGISTRIESDDIR}/default.yaml (Makefile:161-166). Those two files are
# already owned by the containers-common package in this tree, which installs
# /etc/containers/policy.json and /etc/containers/registries.conf. Running the
# top-level target would put two packages in charge of the same paths, and the
# signature policy — the file that decides which images are trusted — is the
# worst possible file to have two owners for. So the binary, docs and
# completions are installed through their own targets and the configuration is
# left to its owner.

configure() {
    set -e
    true
}

build() {
    set -e
    export GOTOOLCHAIN=local
    export CGO_ENABLED=1
    export GO111MODULE=on
    export GOFLAGS="-trimpath -mod=vendor -modcacherw"

    make bin/skopeo -j${IGOS_JOBS} GOFLAGS="${GOFLAGS}"
    make docs
}

do_install() {
    set -e
    make install-binary install-docs install-completions \
        DESTDIR="$DESTDIR"                               \
        PREFIX=/usr                                      \
        BINDIR=/usr/bin
}
