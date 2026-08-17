#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# ibus 1.5.33 — Intelligent Input Bus framework
# BLFS 13.0

configure() {
    set -e
    # Install Unicode Character Database if not already present
    # ibus configure requires UCD files at /usr/share/unicode/ucd/
    #
    # This extraction targets the LIVE tree deliberately: configure reads the
    # UCD from the real path while the package is still being configured, so
    # there is nothing staged yet for it to read. do_install stages the same
    # pinned zip into DESTDIR so the payload is archived and manifest-owned —
    # see the note there. Both copies come from the one sha256-pinned
    # source[1], so they are identical by construction.
    if [ ! -f /usr/share/unicode/ucd/NamesList.txt ]; then
        if [ -f "${IGOS_SOURCES}/UCD.zip" ]; then
            mkdir -p /usr/share/unicode/ucd
            unzip -o "${IGOS_SOURCES}/UCD.zip" -d /usr/share/unicode/ucd
        fi
    fi

    # BLFS required fixes
    sed '/docs/d;/GTK_DOC/d' -i Makefile.am configure.ac
    # Fix deprecated GSettings schema path
    sed -e 's@/desktop/ibus@/org/freedesktop/ibus@g' \
        -i data/dconf/org.freedesktop.ibus.gschema.xml

    # Handle missing gtkdocize
    if ! command -v gtkdocize &>/dev/null; then
        sed -e 's/gtkdocize/true/' -i autogen.sh
        export GTKDOCIZE=true
    fi

    SAVE_DIST_FILES=1 NOCONFIGURE=1 ./autogen.sh

    ./configure --prefix=/usr \
                --sysconfdir=/etc \
                --disable-python2 \
                --disable-appindicator \
                --disable-gtk2 \
                --disable-emoji-dict
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Stage the Unicode Character Database into DESTDIR so the package owns it.
    #
    # configure() unzips the UCD onto the live tree because that is the only
    # place configure can read it from (above). Until now that was the ONLY
    # copy: `make install` does not carry the UCD, so ~40 MB of real payload
    # reached every image with no manifest recording it, and the squashfs
    # ownership gate needed a standing allowlist exception to let the build
    # through. Staging it here puts the same bytes in the archive, so the
    # builder's manifest records them and the exception is no longer owed.
    #
    # The zip is source[1] with a pinned sha256, so verify-sources has already
    # fail-closed on a missing or altered file by the time any build phase
    # runs. An absent zip here is therefore a real defect in the staging, not
    # an expected condition, and it fails loudly rather than shipping a
    # package that silently claims a Unicode database it does not carry.
    if [ ! -f "${IGOS_SOURCES}/UCD.zip" ]; then
        echo "FATAL: ${IGOS_SOURCES}/UCD.zip is absent, but it is a pinned source[1] that verify-sources should have staged — refusing to build an ibus that does not carry the Unicode database it declares" >&2
        exit 1
    fi
    install -dm755 "${DESTDIR}/usr/share/unicode/ucd"
    unzip -q -o "${IGOS_SOURCES}/UCD.zip" -d "${DESTDIR}/usr/share/unicode/ucd"

    # The gate that matters is the one that proves the payload landed: assert
    # the file ibus's own configure looks for, so a zip whose layout changes
    # upstream cannot quietly produce an empty directory.
    if [ ! -f "${DESTDIR}/usr/share/unicode/ucd/NamesList.txt" ]; then
        echo "FATAL: UCD.zip extracted no NamesList.txt into ${DESTDIR}/usr/share/unicode/ucd — the zip layout changed; fix the staging rather than shipping an unowned live-tree copy" >&2
        exit 1
    fi
}

post_install() {
    set -e
    glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
}
