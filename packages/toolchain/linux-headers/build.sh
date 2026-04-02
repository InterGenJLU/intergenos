#!/bin/bash
# Linux 6.18.10 API Headers
# LFS 13.0 Section 5.4
#
# Exposes the kernel's API for use by Glibc.
# No compilation needed — just header extraction and installation.

configure() {
    # Clean any stale files from the source tree
    make mrproper
}

build() {
    # Generate sanitized kernel headers
    make headers

    # Remove non-header files from the output
    find usr/include -type f ! -name '*.h' -delete
}

install() {
    # Install headers to the target system root
    mkdir -pv $IGOS/usr/include
    cp -rv usr/include/* $IGOS/usr/include/
}
