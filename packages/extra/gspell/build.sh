#!/bin/bash
# gspell 1.14.2 — Spell checking library for GTK applications
# BLFS 13.0

configure() {
    set -e
    mkdir gspell-build
    cd    gspell-build

    meson setup ..              \
          --prefix=/usr         \
          --libdir=/usr/lib     \
          --buildtype=release   \
          -D gtk_doc=false
}

build() {
    set -e
    cd gspell-build
    ninja
}

check() {
    set -e
    cd gspell-build
    ninja test || true
}

do_install() {
    set -e
    cd gspell-build
    DESTDIR="$DESTDIR" ninja install
}
