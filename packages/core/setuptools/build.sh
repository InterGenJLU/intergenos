#!/bin/bash
# Setuptools 82.0.0
# LFS 13.0 Section 8.56

configure() {
    : # No configure step
}

build() {
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

install() {
    pip3 install --no-index --no-user --find-links dist setuptools
}
