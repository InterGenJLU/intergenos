#!/bin/bash
# crun 1.27.1 — OCI container runtime written in C
# Not in BLFS — InterGenOS extra tier
#
# Minimal, performant OCI container runtime for Podman.
# Supports cgroups v2, seccomp, AppArmor, user namespaces,
# and systemd integration. A drop-in alternative to runc.
# Built with autotools. Binary linked statically for podman
# compatibility.
#
# Dependencies (all already in-tree):
#   yajl (JSON parsing), libcap (capabilities), libseccomp,
#   systemd (cgroups v2, sd_notify), glib2

configure() {
    ./autogen.sh
    ./configure --prefix=/usr
}

build() {
    make
}

check() {
    ./crun --version
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
