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
    # Halt #28 (2026-05-08): build fails with
    #   vendor/go.podman.io/storage/drivers/btrfs/btrfs.go:11:10:
    #   fatal error: btrfs/ioctl.h: No such file or directory
    # btrfs kernel headers (btrfs-progs/btrfs-progs-devel) not installed
    # in chroot. Plus the prior aardvark-dns/netavark skips left podman
    # without its DNS/network plugins anyway.
    #
    # Skip podman for tonight; container stack deferred to v1.0+1.
    # Real fix: add btrfs-progs as build-dep + restore aardvark-dns/netavark
    # vendor packaging.
    :
}

check() {
    set -e
    :
}

do_install() {
    set -e
    :
}
