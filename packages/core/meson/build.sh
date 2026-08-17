#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Meson 1.10.1
# LFS 13.0 Section 8.59
#
# DESTDIR exception: pip uses --root instead of DESTDIR.
# Shell completions are installed manually.

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    pip3 wheel -w dist --no-cache-dir --no-build-isolation --no-deps $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-index --no-user --root="$DESTDIR" --no-deps --find-links dist meson
    install -vDm644 data/shell-completions/bash/meson "${DESTDIR}/usr/share/bash-completion/completions/meson"
    install -vDm644 data/shell-completions/zsh/_meson "${DESTDIR}/usr/share/zsh/site-functions/_meson"
}
