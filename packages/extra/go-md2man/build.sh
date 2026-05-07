#!/bin/bash
# go-md2man 2.0.7 — Convert Markdown to man pages (roff)
# Not in BLFS — InterGenOS extra tier
#
# Builds the go-md2man tool from the Go module proxy.
# Requires Go 1.26.2 (already in-tree as packages/extra/go/).
# Produces a single binary: go-md2man.
# Used by conmon, containers-common, and other packages to
# generate man pages from markdown sources.

configure() {
    set -e
    :
}

build() {
    set -e
    go install github.com/cpuguy83/go-md2man/v2@v2.0.7
}

check() {
    set -e
    go-md2man --help 2>&1 || "$HOME/go/bin/go-md2man" --help 2>&1
}

do_install() {
    set -e
    install -d "$DESTDIR/usr/bin"
    if [ -x "$GOPATH/bin/go-md2man" ] || [ -x "$HOME/go/bin/go-md2man" ]; then
        BIN="${GOPATH:-$HOME/go}/bin/go-md2man"
        install -v -m755 "$BIN" "$DESTDIR/usr/bin/go-md2man"
    fi
}
