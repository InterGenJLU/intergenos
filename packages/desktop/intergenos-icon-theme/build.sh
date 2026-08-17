#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# packages/desktop/intergenos-icon-theme/build.sh
#
# InterGenOS first-party icon theme — 283 marks (applications + places +
# shell status/symbolic) compiled by the in-house icon compiler (igic
# 0.2.0) into a freedesktop icon theme (index.theme
# Inherits=Adwaita,hicolor; 16..256px + scalable,
# apps/devices/mimetypes/places/status/actions contexts).
#
# Source bundle: assets/InterGenOS.tar.gz (asset-in-package, same
# convention as cybernetic-icon-theme). The tarball ships exactly one
# top-level directory "InterGenOS/". Regeneration: recompile the icon
# corpus with the compiler, re-tar with --sort=name --owner=0 --group=0
# --numeric-owner --mtime='2026-07-29 00:00Z' piped through `gzip -n`
# (pinned entry mtimes + no gzip name/timestamp = a fresh compile
# reproduces the archive bytes exactly; verified by double-build), and
# update EXPECTED_SHA256 in the same commit (atomic provenance).

EXPECTED_SHA256="063ad283dbdb4bca2d25a54ef977f9126d9ea560a72b03ae58358819cbb632e3"

configure() { :; }
build() { :; }

do_install() {
    set -e
    local assets="${ASSETS}"
    if [ -z "$assets" ] || [ ! -d "$assets" ]; then
        # build.sh is sourced; use ${BASH_SOURCE[0]} (not $0 which is the
        # calling chroot-build script).
        assets="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assets"
    fi

    # Integrity gate: the committed bundle must match the recorded pin —
    # halts loud on silent corruption or an un-updated pin after a
    # regeneration.
    local actual
    actual="$(sha256sum "${assets}/InterGenOS.tar.gz" | cut -d' ' -f1)"
    if [ "$actual" != "$EXPECTED_SHA256" ]; then
        echo "FATAL: assets/InterGenOS.tar.gz sha256 mismatch" >&2
        echo "  expected: $EXPECTED_SHA256" >&2
        echo "  actual:   $actual" >&2
        exit 1
    fi

    install -dm755 "${DESTDIR}/usr/share/icons"
    tar -xzf "${assets}/InterGenOS.tar.gz" -C "${DESTDIR}/usr/share/icons/"

    # Normalize modes: tar preserves the capture source's bits, and a
    # sibling bundle shipped its theme root 0770 that way. 755/644 always.
    find "${DESTDIR}/usr/share/icons" -type d -exec chmod 755 {} +
    find "${DESTDIR}/usr/share/icons" -type f -exec chmod 644 {} +

    # Defensive assertion: halt if the bundle structure drifted.
    if [ ! -f "${DESTDIR}/usr/share/icons/InterGenOS/index.theme" ]; then
        echo "FATAL: InterGenOS/index.theme missing in DESTDIR" >&2
        echo "Bundle structure may have changed; verify the tarball" >&2
        exit 1
    fi
}

post_install() {
    set -e
    # Generate the icon-cache for the theme. Without this, GNOME falls back
    # to scanning the entire theme dir on every icon lookup -- noticeable
    # startup lag on slower disks.
    gtk-update-icon-cache -q "/usr/share/icons/InterGenOS" 2>/dev/null || true
}
