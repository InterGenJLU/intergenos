#!/bin/bash
# pinentry-pass1 1.3.2 — PIN/passphrase entry dialog (pass 1 — without gcr/GNOME3 frontend)
#
# Cycle-break for gnupg2 ↔ pinentry ↔ gcr: gnupg2 needs pinentry binary; gcr
# needs gnupg2; full pinentry's GNOME3 frontend needs gcr. Build pass1 first
# (no GNOME3, no gcr dep), then gnupg2, then gcr, then full pinentry
# (which supersedes pass1).

configure() {
    set -e
    # BLFS required fixes (same as full pinentry)
    sed -i "/FLTK 1/s/3/4/" configure
    sed -i '14456 s/1.3/1.4/' configure
    ./configure --prefix=/usr \
                --enable-pinentry-tty \
                --enable-pinentry-curses \
                --disable-pinentry-gnome3
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
