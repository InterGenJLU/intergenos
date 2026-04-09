#!/bin/bash
# gspell 1.14.2 — Spell checking library for GTK applications
# BLFS 13.0

configure() {
    mkdir gspell-build
    cd    gspell-build

    meson setup ..              \
          --prefix=/usr         \
          --buildtype=release   \
          -D gtk_doc=false
}

build() {
    cd gspell-build
    ninja
}

check() {
    cd gspell-build
    ninja test || true
}

do_install() {
    cd gspell-build
    DESTDIR="$DESTDIR" ninja install
}
