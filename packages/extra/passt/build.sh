#!/bin/bash
# passt 2026_01_20.386b5f5 — User-mode networking for VMs and namespaces
# Not in BLFS — InterGenOS extra tier
#
# passt (Plug A Simple Socket Transport) provides user-mode networking
# for qemu/KVM virtual machines. pasta (Pack A Subtle Tap Abstraction)
# is the same binary operating in network-namespace mode.
#
# Zero external dependencies beyond libc. Ships man pages in-tree.
# Date-based versioning; builds passt, pasta, qrap, and passt-repair.
# AVX2-optimized variants built automatically on x86_64.

configure() {
    set -e
    :
}

build() {
    set -e
    make
}

check() {
    set -e
    ./passt --version
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" prefix=/usr install
}
