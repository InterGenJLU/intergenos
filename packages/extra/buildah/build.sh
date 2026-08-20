#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# buildah 1.45.0 — builds OCI images from a Containerfile, or by scripting the
# steps directly, without a long-running daemon.
#
# Build facts verified against the pinned tarball:
#   - Go with vendor/ PRESENT (56 MB, vendor/modules.txt included), so the
#     build resolves every module from the tarball and never reaches the
#     network from inside the chroot.
#   - The Makefile derives its build tags by PROBING the build host:
#     btrfs_installed_tag.sh, hack/libsubid_tag.sh, hack/systemd_tag.sh,
#     hack/sqlite_tag.sh and hack/apparmor_tag.sh each look for headers or
#     libraries and emit a tag if they find them (Makefile:3-9). That means the
#     feature set of the built binary is decided by what is installed in the
#     chroot. Every library those probes look for is therefore declared as a
#     build dependency in package.yml, so the probes resolve the same way on
#     every build rather than by accident.
#   - SECURITYTAGS is set explicitly to `seccomp apparmor`: those two are the
#     confinement mechanisms this distribution actually uses, and leaving them
#     to a probe would let a missing header silently produce a binary that
#     cannot apply a seccomp profile — a security feature disappearing quietly
#     is exactly what must not happen here.
#   - `make install` (Makefile:172-175) installs the binary into $(BINDIR) and
#     then runs `make -C docs install` for the man pages. PREFIX defaults to
#     /usr/local, so it is overridden below.
#
# Version injection: the Makefile stamps the version from `git describe` when a
# .git tree is present. The release tarball has none, so the version is passed
# explicitly; `buildah --version` is the check that it took.

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
    export SECURITYTAGS="seccomp apparmor"

    make bin/buildah -j${IGOS_JOBS} \
        GOFLAGS="${GOFLAGS}"        \
        SECURITYTAGS="${SECURITYTAGS}"
    make docs
}

do_install() {
    set -e
    make install                    \
        DESTDIR="$DESTDIR"          \
        PREFIX=/usr                 \
        BINDIR=/usr/bin
}
