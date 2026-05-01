#!/bin/bash
# gnu-efi 3.0.18 — UEFI development headers and libraries
# Required by efitools, sbsigntool, and shim for EFI binary builds
# Upstream: https://sourceforge.net/projects/gnu-efi/

configure() {
    # gnu-efi uses a plain Makefile, no autotools.
    # The Makefile.defaults sets reasonable defaults for x86_64.
    # Override PREFIX to install into the staging tree.
    :
}

compile() {
    make -j${IGOS_JOBS}
}

check() {
    # gnu-efi has no test suite
    :
}

do_install() {
    make install PREFIX="${DESTDIR}/usr"
}
