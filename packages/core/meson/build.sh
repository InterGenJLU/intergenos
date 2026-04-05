#!/bin/bash
# Meson 1.10.1
# LFS 13.0 Section 8.59
#
# DESTDIR exception: pip uses --root instead of DESTDIR.
# Shell completions are installed manually.

configure() {
    : # No configure step
}

build() {
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

do_install() {
    pip3 install --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist meson
    install -vDm644 data/shell-completions/bash/meson "${DESTDIR}/usr/share/bash-completion/completions/meson"
    install -vDm644 data/shell-completions/zsh/_meson "${DESTDIR}/usr/share/zsh/site-functions/_meson"
}
