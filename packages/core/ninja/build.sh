#!/bin/bash
# Ninja 1.13.2
# LFS 13.0 Section 8.58
#
# DESTDIR exception: Ninja has no install target.
# All files are placed manually with install commands.

configure() {
    set -e
    : # No configure step — uses Python build script
}

build() {
    set -e
    python3 configure.py --bootstrap --verbose
}

# check() — tests require cmake which isn't available during core build.
# The bootstrap build already validates ninja's core functionality.

do_install() {
    set -e
    # Manual installation — no make install target exists
    install -vm755 -d "${DESTDIR}/usr/bin"
    install -vm755 ninja "${DESTDIR}/usr/bin/"
    install -vDm644 misc/bash-completion "${DESTDIR}/usr/share/bash-completion/completions/ninja"
    install -vDm644 misc/zsh-completion "${DESTDIR}/usr/share/zsh/site-functions/_ninja"
}
