#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
# SOURCES.md is installed from the repository root, which is the single
# authored copy; this package keeps no copy of it. See do_install.
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

    # LICENSE is installed from this package's own directory (synced into
    # the chroot via sync_chroot_scripts' packages/ rsync). It is also the
    # licence source scripts/pkg-functions.sh reads in-chroot, so it stays
    # here. Mirrors the intergenos-keyring pre-built-artifact pattern
    # (build-rules §2.5) for a plain-text asset needing no build tooling.
    install -Dm644 /mnt/intergenos/packages/core/intergenos-legal/LICENSE \
        "${DESTDIR}/usr/share/doc/intergenos/LICENSE"

    # SOURCES.md is installed from the REPO-ROOT file — the one authored
    # copy — never from a copy kept inside this package. Decided
    # 2026-08-19: the in-package copy was hand-carried and had drifted 113
    # diff lines from the root file, so installed systems were handed a
    # source-availability statement that named a release and a path that
    # do not exist (/etc/intergenos-release, intergenos-1.0-stable.iso).
    # A copy nothing compares is a copy that goes stale; generating the
    # shipped file from the authored one ends the class.
    #
    # The chroot's /mnt/intergenos is a COPY, not a bind: the file is
    # placed there by phase_setup and sync_chroot_scripts in
    # scripts/build-intergenos.sh, both of which copy it explicitly. This
    # check fails the build loudly and names them rather than installing
    # nothing, because a legal-notice file missing from an installed
    # system is a licence-compliance defect that nothing downstream would
    # report.
    local root_sources="/mnt/intergenos/SOURCES.md"
    if [ ! -f "$root_sources" ]; then
        echo "intergenos-legal: $root_sources missing inside the chroot —" >&2
        echo "  the repo-root SOURCES.md is copied in by phase_setup and" >&2
        echo "  sync_chroot_scripts (scripts/build-intergenos.sh); one of" >&2
        echo "  those did not run or no longer copies it." >&2
        return 1
    fi
    install -Dm644 "$root_sources" \
        "${DESTDIR}/usr/share/doc/intergenos/SOURCES.md"
}
