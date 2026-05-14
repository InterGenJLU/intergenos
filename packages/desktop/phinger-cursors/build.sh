#!/bin/bash
# phinger-cursors 2.1 — phinger cursor theme (4 variants: light, dark, left-handed mirrors)
#
# Upstream: github.com/phisch/phinger-cursors v2.1
# License: GPL-3.0-or-later
#
# Installs four cursor-theme directories at /usr/share/icons/:
#   * phinger-cursors-light       (default, right-handed)
#   * phinger-cursors-dark        <-- referenced by Nordic welcomer combo
#   * phinger-cursors-light-left  (left-handed mirror)
#   * phinger-cursors-dark-left   (left-handed mirror)
#
# Source provenance: the upstream v2.1 release ships
# phinger-cursors-variants.tar.bz2 containing all four variants as
# top-level directories. cp -a is sufficient.

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
    for theme in phinger-cursors-light phinger-cursors-dark phinger-cursors-light-left phinger-cursors-dark-left ; do
        if [ ! -d "${theme}" ] ; then
            echo "phinger-cursors: tarball is missing expected variant '${theme}'" >&2
            exit 1
        fi
        cp -a "${theme}" "${DESTDIR}/usr/share/icons/"
    done
}

post_install() {
    set -e
    :
}
