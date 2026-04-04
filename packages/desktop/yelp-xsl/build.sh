#!/bin/bash
# yelp-xsl 49.0 — GNOME help stylesheets
# BLFS 13.0

configure() {
    ./configure --prefix=/usr
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
