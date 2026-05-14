#!/bin/bash
# bibata-cursor-theme 2.0.7 — Bibata material-design cursor theme (3 variants)
#
# Upstream: github.com/ful1e5/Bibata_Cursor v2.0.7
# License: GPL-3.0-or-later
#
# Installs three cursor-theme directories at /usr/share/icons/:
#   * Bibata-Modern-Classic  <-- system default + 4 welcomer combos
#   * Bibata-Modern-Amber    <-- referenced by Dracula welcomer combo
#   * Bibata-Modern-Ice      <-- referenced by Catppuccin Mocha welcomer combo
#
# Source provenance: the upstream v2.0.7 release ships pre-built variants
# as separate tar.xz files (Bibata-Modern-Classic.tar.xz +
# Bibata-Modern-Amber.tar.xz + Bibata-Modern-Ice.tar.xz, each ~1.7-1.9 MB).
# We bundle the three we ship into a single reproducible tarball
# (bibata-cursor-theme-2.0.7.tar.xz, 5.1 MB) generated locally with
# SOURCE_DATE_EPOCH-pinned mtimes for byte-identical builds. The bundle
# tarball is staged at the build mirror; do_install copies each theme
# directory to /usr/share/icons/.
#
# The fourth Bibata variant (Original-Classic) is intentionally not
# shipped — it is a stylistic legacy variant that no welcomer combo
# references. If user demand surfaces, expose it via a follow-on
# package.

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
    for theme in Bibata-Modern-Classic Bibata-Modern-Amber Bibata-Modern-Ice ; do
        if [ ! -d "${theme}" ] ; then
            echo "bibata-cursor-theme: bundle is missing expected variant '${theme}'" >&2
            exit 1
        fi
        cp -a "${theme}" "${DESTDIR}/usr/share/icons/"
    done
}

post_install() {
    set -e
    # XCursor themes have no compiled cache — they are read on demand
    # by the X cursor loader (XCURSOR_PATH search). No post_install
    # action is needed; this hook is present for convention.
    :
}
