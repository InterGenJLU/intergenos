#!/bin/bash
# pinentry-pass1 1.3.2 — PIN/passphrase entry dialog (bootstrap, TTY/curses only)
# First pass of 2-pass build. TTY + curses frontends only; no GNOME3/GTK/Qt/FLTK
# frontends. Satisfies gnupg2 in tier:core without pulling libsecret/gcr
# (tier:desktop) into the core build. Full pinentry with GNOME3 frontend
# lives in tier:desktop and supersedes via migrate-pkm-supersedes.sh.

configure() {
    set -e
    # BLFS required fixes (same as full build)
    sed -i "/FLTK 1/s/3/4/" configure
    sed -i '14456 s/1.3/1.4/' configure

    ./configure --prefix=/usr \
                --enable-pinentry-tty \
                --enable-pinentry-curses \
                --disable-pinentry-gnome3 \
                --disable-pinentry-gtk2 \
                --disable-pinentry-qt \
                --disable-pinentry-qt5 \
                --disable-pinentry-fltk
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
