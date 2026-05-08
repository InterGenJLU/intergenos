#!/bin/bash
# btrfs-progs 6.19.1 — Userspace utilities and headers for Btrfs
#
# Provides /usr/include/btrfs/{ioctl.h,...} which podman's containers/storage
# btrfs driver requires (Build #5 audit Halt #28). Also installs btrfs(8),
# btrfs-convert, mkfs.btrfs, etc.
#
# Build #5 audit: not in package set → podman skipped because btrfs/ioctl.h
# could not be located.
#
# Notes:
#   - Upstream tarball ships ioctl.h at libbtrfs/ioctl.h (not include/).
#     `make install` rewrites the path to /usr/include/btrfs/ioctl.h, which
#     is what podman includes as <btrfs/ioctl.h>.
#   - --disable-static keeps install footprint sane while preserving headers.
#   - --disable-documentation skips sphinx (not in tree); manpages still
#     install via separate target.

configure() {
    set -e
    ./configure --prefix=/usr            \
                --disable-static         \
                --disable-documentation
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make test
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    # Headers go to /usr/include/btrfs/ via `make install-headers`. Some
    # versions don't run install-headers as part of the default install
    # target — run it explicitly to be safe.
    make DESTDIR="$DESTDIR" install-headers 2>/dev/null || true
}
