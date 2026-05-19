#!/bin/bash
# intergenos-legal 0.1.0 — InterGenOS legal documents
# https://github.com/InterGenJLU/intergenos
#
# Installs: /usr/share/doc/intergenos/LICENSE and /usr/share/doc/intergenos/SOURCES.md
#
# Why: GPL §6 (and equivalent provisions in LGPL, MPL, AGPL, EPL) requires
# that corresponding source availability "accompany" the binary distribution.
# LICENSE + SOURCES.md sitting only in the upstream git repo does not
# "accompany" the binary an end user installs on their machine. This
# package puts both files on every installed system so a recipient can
# find the project's source-availability commitment locally without
# needing network access to GitHub.
#
# THIRD-PARTY-NOTICES will join this package when the legal sprint
# follow-up emits it (currently per-package LICENSE bundling is tracked
# at audit row P-004 / P-010 / P-014).

build() {
    set -e
    # No build step — pure-data package.
    return 0
}

do_install() {
    set -e
    install -dm755 "${DESTDIR}/usr/share/doc/intergenos"

    install -Dm644 /mnt/intergenos/LICENSE \
        "${DESTDIR}/usr/share/doc/intergenos/LICENSE"
    install -Dm644 /mnt/intergenos/SOURCES.md \
        "${DESTDIR}/usr/share/doc/intergenos/SOURCES.md"
}
