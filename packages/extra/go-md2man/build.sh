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
    # Both tarballs extract into the same source root. The project tarball
    # provides go.mod/go.sum/source files; the vendor tarball provides
    # vendor/. Standard offline Go build pattern.
    :
}

build() {
    set -e
    # GOFLAGS=-mod=vendor + offline modules cache. Project's only direct
    # dep is github.com/russross/blackfriday/v2 (vendored).
    export GOFLAGS="-mod=vendor"
    export GOPROXY=off
    go build -ldflags '-s -w' -o go-md2man .
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        env GOFLAGS=-mod=vendor GOPROXY=off go test ./...
}

do_install() {
    set -e
    install -Dm755 go-md2man "$DESTDIR/usr/bin/go-md2man"
}
