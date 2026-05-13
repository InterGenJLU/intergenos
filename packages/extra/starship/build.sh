#!/bin/bash
# starship 1.25.1 — minimal, customizable shell prompt
# Upstream: https://starship.rs
# License: ISC
#
# Bumped 1.18.2 → 1.25.1 for rustc 1.95 compat (older time-0.3.x crate
# pinned in 1.18.2 lockfile fails E0282 type inference on Box<_>).
#
# Completion model: starship has a 'completions <shell>' subcommand
# that prints to stdout (no pre-generated completion files in source).
# We invoke the just-built binary at install time to capture them.
#
# Man page: starship does NOT ship one upstream — primary user interface
# is the 'starship init <shell>' eval pattern documented at
# starship.rs/installing. Skip man (genuine — upstream gap, not laziness;
# Arch + Fedora + Nix all skip too).

configure() {
    set -e
    tar xf "$IGOS_SOURCES/starship-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/starship "$DESTDIR/usr/bin/starship"

    # Shell completions via the just-built binary's `completions` subcommand.
    install -d -m 755 "$DESTDIR/usr/share/bash-completion/completions"
    target/release/starship completions bash > \
        "$DESTDIR/usr/share/bash-completion/completions/starship"

    install -d -m 755 "$DESTDIR/usr/share/fish/vendor_completions.d"
    target/release/starship completions fish > \
        "$DESTDIR/usr/share/fish/vendor_completions.d/starship.fish"

    install -d -m 755 "$DESTDIR/usr/share/zsh/site-functions"
    target/release/starship completions zsh > \
        "$DESTDIR/usr/share/zsh/site-functions/_starship"
}
