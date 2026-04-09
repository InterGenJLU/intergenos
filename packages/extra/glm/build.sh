#!/bin/bash
# glm 1.0.3 — OpenGL Mathematics (header-only library)
# BLFS 13.0 — just copy headers into position

configure() {
    # Header-only library — no configure needed
    :
}

build() {
    # Header-only library — no build needed
    :
}

do_install() {
    # BLFS: copy headers directly
    cp -r glm "${DESTDIR}/usr/include/"

    # Install documentation
    install -v -d -m755 "${DESTDIR}/usr/share/doc/glm-${PKG_VERSION}"
    cp -r doc/* "${DESTDIR}/usr/share/doc/glm-${PKG_VERSION}/"
}
