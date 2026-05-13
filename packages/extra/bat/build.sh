#!/bin/bash
# bat 0.26.1 — cat(1) clone with syntax highlighting + git integration
# Upstream: https://github.com/sharkdp/bat
# License: MIT OR Apache-2.0

configure() {
    set -e
    tar xf "$IGOS_SOURCES/bat-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/bat "$DESTDIR/usr/bin/bat"

    # bat's build script (build/application.rs) generates the man page and
    # shell completions at build time, into a hash-named OUT_DIR under
    # target/release/build/bat-<hash>/out/. Locate dynamically + install
    # to FHS-canonical paths.
    local out_dir
    out_dir=$(find target/release/build -maxdepth 3 -path '*/bat-*/out' -type d | head -1)
    if [ -z "$out_dir" ] || [ ! -d "$out_dir/assets" ]; then
        echo "ERROR: bat OUT_DIR with assets/ not found under target/release/build/" >&2
        exit 1
    fi

    install -Dm644 "$out_dir/assets/manual/bat.1" \
        "$DESTDIR/usr/share/man/man1/bat.1"
    install -Dm644 "$out_dir/assets/completions/bat.bash" \
        "$DESTDIR/usr/share/bash-completion/completions/bat"
    install -Dm644 "$out_dir/assets/completions/bat.fish" \
        "$DESTDIR/usr/share/fish/vendor_completions.d/bat.fish"
    install -Dm644 "$out_dir/assets/completions/bat.zsh" \
        "$DESTDIR/usr/share/zsh/site-functions/_bat"
}
