#!/bin/bash
# pinentry 1.3.2 — PIN/passphrase entry dialog
# BLFS 13.0

configure() {
    set -e
    # BLFS required fixes
    sed -i "/FLTK 1/s/3/4/" configure
    sed -i '14456 s/1.3/1.4/' configure
    # GNOME-3 frontend (uses gcr-3 / libsecret) is the canonical InterGenOS
    # graphical pinentry. Without it, GPG passphrase prompts on a Wayland-only
    # GNOME desktop fall back to a TTY that may not exist in a graphical
    # session — Build #5 audit finding.
    ./configure --prefix=/usr \
                --enable-pinentry-tty \
                --enable-pinentry-curses \
                --enable-pinentry-gnome3
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
