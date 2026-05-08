#!/bin/bash
# podman 5.8.2 — Tool and library for managing OCI containers and pods
# Not in BLFS — InterGenOS extra tier
#
# Full container runtime with Docker-compatible CLI.
# Built with Go 1.26.2 using the project's Makefile.
# Dependencies (all shipped today in Track P):
#   crun (OCI runtime), netavark + aardvark-dns (network),
#   fuse-overlayfs (storage), catatonit (container init),
#   conmon (monitor), passt (user-mode networking),
#   containers-common (config), yajl (JSON parsing)

configure() {
    set -e
    :
}

build() {
    set -e
    # Standard podman makefile build. Requires btrfs-progs headers
    # (Build #5 Halt #28) — added as build dep in package.yml. Cgo
    # links against /usr/include/btrfs/ioctl.h.
    make BUILDTAGS="seccomp systemd selinux apparmor exclude_graphdriver_devicemapper" \
        -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make localunit
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" PREFIX=/usr install
}
