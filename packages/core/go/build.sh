#!/bin/bash
# go 1.26.2 — The Go programming language compiler and toolchain
# Not in BLFS — InterGenOS extra tier toolchain
#
# Go is self-hosted: compiling Go requires an existing Go installation.
# Bootstrap strategy: extract the upstream binary tarball (go1.26.2.linux-amd64.tar.gz)
# to provide a working Go toolchain. The binary distribution includes the
# compiler, standard library (pre-compiled), and tool source.
#
# For a full source-based rebuild, set GOROOT_BOOTSTRAP to a previous Go
# installation and build from go1.26.2.src.tar.gz with ./make.bash.
# The binary bootstrap path is the default for this recipe.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    tar -xzf "$IGOS_SOURCES/go${PKG_VERSION}.linux-amd64.tar.gz"
    export GOROOT="$PWD/go"
    export PATH="$GOROOT/bin:$PATH"
}

build() {
    set -e
    :
}

check() {
    set -e
    # Each phase is a fresh `source build.sh` invocation — env vars set in
    # configure() are gone. Re-export GOROOT/PATH so go is on PATH here.
    export GOROOT="$PWD/go"
    export PATH="$GOROOT/bin:$PATH"

    go version
    go env GOROOT GOPATH GOARCH GOOS

    local saved="$PWD"
    mkdir -p /tmp/go-test
    cat > /tmp/go-test/hello.go << 'GOEOF'
package main

import "fmt"

func main() {
    fmt.Println("hello, InterGenOS")
}
GOEOF
    cd /tmp/go-test
    go build -o hello hello.go
    ./hello
    cd "$saved"
    rm -rf /tmp/go-test
}

do_install() {
    set -e
    install -d "$DESTDIR/usr/lib"
    cp -a go "$DESTDIR/usr/lib/go"
    rm -rf "$DESTDIR/usr/lib/go/pkg/bootstrap"
    rm -rf "$DESTDIR/usr/lib/go/pkg/obj"

    install -d "$DESTDIR/usr/bin"
    for bin in go gofmt; do
        ln -sf ../lib/go/bin/$bin "$DESTDIR/usr/bin/$bin"
    done

    install -d "$DESTDIR/usr/share/man/man1"
    install -v -m644 "$BUILD_DIR/go.1" "$DESTDIR/usr/share/man/man1/go.1"
}
