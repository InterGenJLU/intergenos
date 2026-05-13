#!/bin/bash
# scons 4.10.1 — Pure-Python software build tool
# Upstream: https://github.com/SCons/scons
# License: MIT
# Source: GitHub tag 4.10.1 (2025-11-16; 177 days pre-attack-window)
# PyPI attack-window discipline: zero-PyPI methodology.
# This package fetches from GitHub source, NOT from PyPI.
# SCons is a build-time tool only; never invoked at runtime.
# No daemons, no privileged surface, no network exposure.
#
# Build method: scons 4.10.1 uses pyproject.toml with setuptools.build_meta
# backend (PEP 517). The legacy `setup.py install --standard-lib` /
# `--no-version-script` flags do not exist anymore — setup.py is a minimal
# shim. Install via pip wheel + pip install matching the numpy precedent
# already established for pyproject.toml-based packages in this tree.
#
# Zero-PyPI guarantees in this build:
#   --no-build-isolation : pip uses system setuptools (no PyPI fetch for build deps)
#   --no-deps            : pip does not fetch any declared deps from PyPI
#   --no-cache-dir       : pip writes to no on-disk cache
#   --no-index           : install step disables PyPI entirely
#   --find-links dist    : install step resolves only from the just-built local wheel

configure() {
    set -e
    true
}

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

check() {
    set -e
    true
}

do_install() {
    set -e
    pip3 install --no-index --find-links dist --no-user \
         --root="$DESTDIR" --no-deps scons
}

post_install() {
    set -e
    true
}
