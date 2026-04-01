#!/bin/bash
# Iana-Etc 20260202
# LFS 13.0 Section 8.4
#
# DESTDIR exception: No build system. Just copies files.

configure() {
    : # Nothing to configure
}

build() {
    : # Nothing to build
}

install() {
    mkdir -pv "${DESTDIR}/etc"
    cp -v services protocols "${DESTDIR}/etc"
}
