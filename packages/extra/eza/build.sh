#!/bin/bash
# eza 0.23.4 — modern, maintained replacement for ls
# Upstream: https://github.com/eza-community/eza
# License: EUPL-1.2
#
# Bumped 0.18.11 → 0.23.4 for rustc 1.95 compat (older time-0.3.x crate
# pinned in 0.18.11 lockfile fails E0282 type inference on Box<_>).
#
# Shipped surface:
#   - /usr/bin/eza                                        (binary)
#   - /usr/share/bash-completion/completions/eza          (bash, from completions/bash/)
#   - /usr/share/fish/vendor_completions.d/eza.fish       (fish, from completions/fish/)
#   - /usr/share/zsh/site-functions/_eza                  (zsh, from completions/zsh/)
#   - /usr/share/man/man1/eza.1                           (man, pandoc-rendered, ships in pkg dir)
#   - /usr/share/man/man5/eza_colors.5                    (man, pandoc-rendered)
#   - /usr/share/man/man5/eza_colors-explanation.5        (man, pandoc-rendered)
#
# Man-page generation note: upstream ships man/*.md (markdown sources) and
# uses pandoc to render to roff. Pandoc is NOT in the chroot's tree, so we
# pre-render on SPOC host once (pandoc 3.1.3 from apt) and ship the rendered
# files as static artifacts in this package's directory. At next version
# bump, re-render against the new upstream markdown.

BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

configure() {
    set -e
    tar xf "$IGOS_SOURCES/eza-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/eza "$DESTDIR/usr/bin/eza"

    # Shell completions ship pre-generated in the source tree.
    install -Dm644 completions/bash/eza \
        "$DESTDIR/usr/share/bash-completion/completions/eza"
    install -Dm644 completions/fish/eza.fish \
        "$DESTDIR/usr/share/fish/vendor_completions.d/eza.fish"
    install -Dm644 completions/zsh/_eza \
        "$DESTDIR/usr/share/zsh/site-functions/_eza"

    # Man pages pre-rendered on SPOC; ship from package directory.
    install -Dm644 "$BUILD_DIR/eza.1" \
        "$DESTDIR/usr/share/man/man1/eza.1"
    install -Dm644 "$BUILD_DIR/eza_colors.5" \
        "$DESTDIR/usr/share/man/man5/eza_colors.5"
    install -Dm644 "$BUILD_DIR/eza_colors-explanation.5" \
        "$DESTDIR/usr/share/man/man5/eza_colors-explanation.5"
}
