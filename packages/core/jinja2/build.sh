#!/bin/bash
# Jinja2 3.1.6
# LFS 13.0 Section 8.74

configure() {
    : # No configure step
}

build() {
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

install() {
    pip3 install --no-index --no-user --find-links dist Jinja2
}
