#!/bin/bash
# IPRoute2 6.18.0
# LFS 13.0 Section 8.68

configure() {
    # Remove arpd (requires Berkeley DB, not in LFS)
    sed -i /ARPD/d Makefile
    rm -fv man/man8/arpd.8
}

build() {
    make NETNS_RUN_DIR=/run/netns -j${IGOS_JOBS}
}

install() {
    make DESTDIR="$DESTDIR" SBINDIR=/usr/sbin install
}
