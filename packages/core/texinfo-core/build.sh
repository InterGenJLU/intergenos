#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Texinfo 7.2
# LFS 13.0 Section 8.74

configure() {
    set -e
    # Fix Perl compatibility issue
    sed 's/! $output_file eq/$output_file ne/' -i tp/Texinfo/Convert/*.pm

    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
    make DESTDIR="$DESTDIR" TEXMF=/usr/share/texmf install-tex
}

# Post-install: rebuild info dir on live system
post_install() {
    set -e
    pushd /usr/share/info
    rm -f dir
    # Rebuild the info directory index. On a POPULATED substrate this dir
    # holds more than clean info files: package doc images (gawk/gnutls
    # *.png/*.jpg) and the mingw-w64 per-target SUBDIRECTORIES — and
    # install-info on a directory exits 1, which under set -e killed this
    # hook on the first targeted rebuild off a full chroot (Q9 leg A,
    # 2026-07-21; a from-scratch build never sees them at this phase).
    # Skip non-regular-file entries and doc images explicitly; surface any
    # other install-info failure loudly (no stderr mask) without letting a
    # single odd entry kill the rebuild; fail closed if NOTHING indexed.
    for f in *; do
        [ -f "$f" ] || continue
        case "$f" in *.png|*.jpg|dir) continue ;; esac
        install-info "$f" dir || echo "post_install: install-info skipped $f (rc=$?)" >&2
    done
    test -s dir
    popd
}
