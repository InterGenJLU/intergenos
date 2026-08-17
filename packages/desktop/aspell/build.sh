#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# aspell 0.60.8.2 — Interactive spell checking program and libraries
# BLFS 13.0

configure() {
    set -e
    ./configure --prefix=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    ln -svfn aspell-0.60 "${DESTDIR}/usr/lib/aspell"
    install -v -m755 -d "${DESTDIR}/usr/share/doc/aspell-0.60.8.2/aspell.html"
    install -v -m644 manual/aspell.html/* \
        "${DESTDIR}/usr/share/doc/aspell-0.60.8.2/aspell.html"
    install -v -m644 manual/{aspell,spell-checking}.pdf \
        "${DESTDIR}/usr/share/doc/aspell-0.60.8.2" 2>/dev/null || true

    # --- English dictionary, staged into the PACKAGE -----------------------
    # The dictionary is source[1] of this recipe, sha-pinned, and it used to be
    # compiled by post_install. post_install runs on the LIVE tree after the
    # archive has already been made (igos-build/builder.py fires it once the
    # track block has archived, deployed, verified and registered), so the
    # words landed in the build chroot and in nothing else: no archive carried
    # them, no manifest claimed them, and the ownership allowlist has an
    # `usr/lib/aspell-0.60/**` DEBT entry recording exactly that. Measured on an
    # installed system: no en* files under /usr/lib/aspell-0.60, no such rows in
    # the aspell manifest, and `aspell -l en list` answering "No word lists can
    # be found for the language en" — the shipped spell checker had no words.
    #
    # Same shape and same remedy as glibc-core's zoneinfo: build the artifact
    # into DESTDIR during the install phase so it travels in the archive.
    #
    # The dictionary's build needs aspell itself, which is not yet deployed on a
    # from-scratch build — it exists only in DESTDIR. Point its configure at the
    # staged binaries and let the runtime linker find the staged library. The
    # generated Makefile prefixes ${DESTDIR} to both install dirs, and aspell
    # reports its compiled-in /usr paths, so the words land under DESTDIR at the
    # locations they will occupy on the target.
    #
    # ASPELL_FLAGS carries the staged data dir into every `create master` run.
    # Without it the word-list compiler resolves its charset tables against the
    # compiled-in /usr/lib/aspell-0.60, which on a from-scratch chroot is empty
    # at this point in the build: measured against an emptied data dir as
    # `Error: The file "/usr/lib/aspell-0.60/iso-8859-1.cset" can not be opened
    # for reading` and a failed make. configure probes the install directories
    # with a bare aspell, so the paths it records stay the compiled-in ones and
    # only the compiler's own lookups move.
    local dict_tar="${IGOS_SOURCES}/aspell6-en-2020.12.07-0.tar.bz2"
    if [ ! -f "$dict_tar" ]; then
        echo "FATAL: ${dict_tar} is absent — refusing to build an aspell package with no word lists" >&2
        return 1
    fi
    local dict_src
    dict_src="$(mktemp -d)"
    tar xf "$dict_tar" -C "$dict_src" --strip-components=1
    (
        set -e
        cd "$dict_src"
        export LD_LIBRARY_PATH="${DESTDIR}/usr/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
        ASPELL="${DESTDIR}/usr/bin/aspell" \
        PREZIP="${DESTDIR}/usr/bin/prezip-bin" \
        ASPELL_FLAGS="--data-dir=${DESTDIR}/usr/lib/aspell-0.60" \
            ./configure
        make
        make DESTDIR="${DESTDIR}" install
    )
    rm -rf "$dict_src"

    # The word lists are the point of the package; assert they arrived rather
    # than trusting the sub-build's exit code alone.
    if [ ! -f "${DESTDIR}/usr/lib/aspell-0.60/en-common.rws" ] \
       || [ ! -f "${DESTDIR}/usr/lib/aspell-0.60/en.dat" ]; then
        echo "FATAL: the English dictionary did not stage into ${DESTDIR}/usr/lib/aspell-0.60" >&2
        return 1
    fi
}
