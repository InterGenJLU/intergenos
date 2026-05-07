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
    make PREFIX=/usr BUILDTAGS="seccomp systemd"
}

check() {
    set -e
    bin/podman --version
}

do_install() {
    set -e
    make PREFIX=/usr DESTDIR="$DESTDIR" install
}
