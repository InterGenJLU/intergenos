#!/bin/bash
# ibus 1.5.33 — Intelligent Input Bus framework
# BLFS 13.0

configure() {
    # Fix deprecated GSettings schema path
    sed -e 's@/desktop/ibus@/org/freedesktop/ibus@g' \
        -i data/dconf/org.freedesktop.ibus.gschema.xml

    # Handle missing gtkdocize
    if ! command -v gtkdocize &>/dev/null; then
        sed -e 's/gtkdocize/true/' -i autogen.sh
        export GTKDOCIZE=true
    fi

    SAVE_DIST_FILES=1 NOCONFIGURE=1 ./autogen.sh

    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --disable-python2 \
                --disable-appindicator \
                --disable-gtk2 \
                --disable-emoji-dict
}

build() {
    make -j${IGOS_JOBS}
}

do_install() {
    make DESTDIR="$DESTDIR" install
}

post_install() {
    # Install Unicode Character Database if available
    if [ -f "${IGOS_SOURCES}/UCD.zip" ]; then
        mkdir -p /usr/share/unicode/ucd
        unzip -o "${IGOS_SOURCES}/UCD.zip" -d /usr/share/unicode/ucd
    fi

    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
