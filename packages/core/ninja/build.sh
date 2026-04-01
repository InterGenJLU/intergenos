#!/bin/bash
# Ninja 1.13.2
# LFS 13.0 Section 8.58
#
# DESTDIR exception: Ninja has no install target.
# All files are placed manually with install commands.

configure() {
    : # No configure step — uses Python build script
}

build() {
    python3 configure.py --bootstrap
}

check() {
    ninja_test=$PWD
    ./ninja -j${IGOS_JOBS} ninja_test
    $ninja_test/ninja_test --gtest_filter=-SubprocessTest.SetWithLots || true
}

do_install() {
    # Manual installation — no make install target exists
    install -vm755 -d "${DESTDIR}/usr/bin"
    install -vm755 ninja "${DESTDIR}/usr/bin/"
    install -vDm644 misc/bash-completion "${DESTDIR}/usr/share/bash-completion/completions/ninja"
    install -vDm644 misc/zsh-completion "${DESTDIR}/usr/share/zsh/site-functions/_ninja"
}
