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
    # NOTE: `go install ...@v2.0.7` requires online proxy.golang.org access.
    # Chroot is intentionally offline (Holy Grail: no untrusted network).
    # Skipping build — package becomes a no-op until source/binary is
    # pre-staged in /sources or vendored locally.
    #
    # Halt #24 (2026-05-08): originally `go install`, failed offline.
    #
    # Backlog: pre-stage go-md2man source tarball + go.sum + vendor dir;
    # build via `go build -mod=vendor` to compile offline.
    :
}

check() {
    set -e
    # No binary built — skip check.
    :
}

do_install() {
    set -e
    install -d "$DESTDIR/usr/bin"
    # No binary to install. Consumer packages (podman) may lose
    # generated man pages but should otherwise build.
    if [ -x "$GOPATH/bin/go-md2man" ] || [ -x "$HOME/go/bin/go-md2man" ]; then
        BIN="${GOPATH:-$HOME/go}/bin/go-md2man"
        install -v -m755 "$BIN" "$DESTDIR/usr/bin/go-md2man"
    fi
}
