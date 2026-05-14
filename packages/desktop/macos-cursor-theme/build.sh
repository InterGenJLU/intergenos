#!/bin/bash
# macos-cursor-theme 2.0.1 — Apple-style cursor theme (macOS variant)
#
# Upstream: github.com/ful1e5/apple_cursor v2.0.1
# License: GPL-3.0-or-later
#
# Installs one cursor-theme directory at /usr/share/icons/:
#   * macOS  <-- referenced by WhiteSur welcomer combo
#
# Upstream apple_cursor v2.0.1 ships two pre-built variants — macOS
# (dark, default theme name) and macOS-White (light). We ship only the
# dark variant since that is what the welcomer's WhiteSur combo
# references. The Light variant can be exposed as a follow-on package
# if user demand surfaces.
#
# Source provenance: upstream v2.0.1's macOS.tar.xz contains the
# theme directory as a top-level dir; cp -a is sufficient.

configure() {
    set -e
    :
}

build() {
    set -e
    :
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/icons"
    if [ ! -d "macOS" ] ; then
        echo "macos-cursor-theme: tarball is missing expected 'macOS' dir" >&2
        exit 1
    fi
    cp -a macOS "${DESTDIR}/usr/share/icons/"
}

post_install() {
    set -e
    :
}
