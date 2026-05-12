#!/bin/bash
# vala-pass1 0.56.18 — Vala compiler (bootstrap, no valadoc)
# First pass of 2-pass build. --disable-valadoc breaks the cycle to
# graphviz (tier:desktop) so libgudev's vala bindings (.vapi files) can
# be generated in core-extra without pulling the desktop stack into
# tier:core. Full vala with valadoc lives in tier:desktop and supersedes
# via migrate-pkm-supersedes.sh.

configure() {
    set -e
    ./configure --prefix=/usr \
                --disable-static \
                --disable-valadoc
}

build() {
    set -e
    make bootstrap -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
