#!/bin/bash
# xh 0.25.3 — friendly and fast tool for sending HTTP requests
# Upstream: https://github.com/ducaale/xh
# License: MIT
#
# Bumped 0.21.0 → 0.25.3 for GCC 14/15 C23-strict compat. The 0.21.0 lockfile
# pinned an older onig_sys vendoring Oniguruma C source with K&R-style
# function pointer declarations (`int (*)(ANYARGS)` resolves to `int (*)(void)`
# under modern C); xh 0.25.3's lockfile ships a patched onig_sys.
#
# Shipped surface:
#   - /usr/bin/xh                                          (binary)
#   - /usr/bin/xhs                                         (symlink — HTTPS shortcut)
#   - /usr/share/bash-completion/completions/xh            (bash, from completions/)
#   - /usr/share/fish/vendor_completions.d/xh.fish         (fish, from completions/)
#   - /usr/share/zsh/site-functions/_xh                    (zsh, from completions/)
#   - /usr/share/man/man1/xh.1                             (man, --generate=man at install)
#
# Completion model: xh ships pre-generated completions/ in the source tree
# (bash, elvish, fish, nushell, zsh + powershell). We install the three
# canonical Linux shells; elvish/nushell/powershell are skipped as non-standard
# (matches Arch + Debian conventions).
#
# Man page: xh has a `--generate=man` runtime subcommand (added in 0.24.0)
# that emits roff to stdout. We invoke the just-built binary at install time
# (starship-style pattern). SOURCE_DATE_EPOCH is honored upstream for
# reproducible mtimes in the generated roff.

configure() {
    set -e
    tar xf "$IGOS_SOURCES/xh-$PKG_VERSION-vendor.tar.xz" --strip-components=1
}

build() {
    set -e
    cargo build --release --frozen --offline
}

do_install() {
    set -e
    install -Dm755 target/release/xh "$DESTDIR/usr/bin/xh"

    # xhs — HTTPS shortcut (upstream convention: `xh` defaults to http://,
    # `xhs` defaults to https://). Symlink rather than separate build to
    # keep the install footprint minimal.
    ln -sf xh "$DESTDIR/usr/bin/xhs"

    # Shell completions ship pre-generated in the source tree.
    install -Dm644 completions/xh.bash \
        "$DESTDIR/usr/share/bash-completion/completions/xh"
    install -Dm644 completions/xh.fish \
        "$DESTDIR/usr/share/fish/vendor_completions.d/xh.fish"
    install -Dm644 completions/_xh \
        "$DESTDIR/usr/share/zsh/site-functions/_xh"

    # Man page via the just-built binary's --generate=man subcommand.
    install -d -m 755 "$DESTDIR/usr/share/man/man1"
    target/release/xh --generate=man > "$DESTDIR/usr/share/man/man1/xh.1"
}
