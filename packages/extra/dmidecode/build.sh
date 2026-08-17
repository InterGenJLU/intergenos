#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# dmidecode 3.7 — decode the system BIOS SMBIOS/DMI hardware records. Plain
# top-level Makefile (no configure); honors prefix + DESTDIR. Not in BLFS 13.0.
#
# On x86_64 (InterGenOS's target) the Makefile builds all four programs: dmidecode
# plus the x86-only biosdecode/ownership/vpddecode. On a non-x86 arch only dmidecode
# would build — the sibling verify_paths entries assume the x86_64 target.

configure() {
    set -e
    :  # plain Makefile — no configure step
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make prefix=/usr DESTDIR="${DESTDIR}" install
}
