#!/bin/bash
# Packaging 26.0
# LFS 13.0 Section 8.54

configure() {
    : # No configure step
}

build() {
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

install() {
    pip3 install --no-index --no-user --find-links dist packaging
}
