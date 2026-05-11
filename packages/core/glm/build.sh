#!/bin/bash
# glm 1.0.3 — OpenGL Mathematics (header-only library)
# BLFS 13.0 — just copy headers into position

configure() {
    set -e
    # Header-only library — no configure needed
    :
}

build() {
    set -e
    # Header-only library — no build needed
    :
}

do_install() {
    set -e
    # BLFS: install headers into glm/ subdirectory (consumers expect <glm/glm.hpp>)
    mkdir -p "${DESTDIR}/usr/include/glm"
    cp -r glm/* "${DESTDIR}/usr/include/glm/"

    # Install documentation
    install -v -d -m755 "${DESTDIR}/usr/share/doc/glm-${PKG_VERSION}"
    cp -r doc/* "${DESTDIR}/usr/share/doc/glm-${PKG_VERSION}/"
}
