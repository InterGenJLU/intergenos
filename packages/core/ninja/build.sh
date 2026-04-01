#!/bin/bash
# Ninja 1.13.2
# LFS 13.0 Section 8.57

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

install() {
    install -vm755 ninja /usr/bin/
    install -vDm644 misc/bash-completion /usr/share/bash-completion/completions/ninja
    install -vDm644 misc/zsh-completion /usr/share/zsh/site-functions/_ninja
}
