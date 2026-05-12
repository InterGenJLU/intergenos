#!/bin/bash
# scons 4.10.1 — Pure-Python software build tool
# Upstream: https://github.com/SCons/scons
# License: MIT
# Source: GitHub tag 4.10.1 (2025-11-16; 177 days pre-attack-window)
# PyPI attack-window discipline: zero-PyPI methodology.
# This package fetches from GitHub source, NOT from PyPI.
# SCons is a build-time tool only; never invoked at runtime.
# No daemons, no privileged surface, no network exposure.

configure() {
    set -e
    true
}

build() {
    set -e
    true
}

check() {
    set -e
    true
}

do_install() {
    set -e
    python3 setup.py install \
        --prefix=/usr \
        --root="${DESTDIR}" \
        --no-version-script \
        --standard-lib

    # Pre-compile .py files for faster startup
    python3 -m compileall "${DESTDIR}/usr/lib"
}

post_install() {
    set -e
    true
}
