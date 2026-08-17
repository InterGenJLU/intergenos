#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# acpica 20260408 — ACPI Component Architecture utilities (iasl)
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# Ships the iasl ACPI table compiler/disassembler plus the ACPI
# inspection tools (acpidump, acpixtract, acpiexec, ...). iasl is a
# build-time dependency of edk2-ovmf (UEFI guest firmware). The unix2
# release tarball is the upstream-published Linux source; plain Makefile
# with PREFIX/DESTDIR install into /usr/bin.

configure() {
    set -e
    # No configure step: plain upstream Makefile (generate/unix).
    :
}

build() {
    set -e
    make -j"$(nproc)" PREFIX=/usr
}

do_install() {
    set -e
    make DESTDIR="${DESTDIR}" PREFIX=/usr install
}
