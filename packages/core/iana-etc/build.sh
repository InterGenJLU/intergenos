#!/bin/bash
# Iana-Etc 20260202
# LFS 13.0 Section 8.4
#
# DESTDIR exception: No build system. Just copies files.

configure() {
    set -e
    : # Nothing to configure
}

build() {
    set -e
    : # Nothing to build
}

do_install() {
    set -e
    mkdir -pv "${DESTDIR}/etc"
    cp -v services protocols "${DESTDIR}/etc"
}
