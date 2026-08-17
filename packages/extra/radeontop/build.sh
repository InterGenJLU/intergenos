#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# radeontop 1.4 — AMD Radeon GPU utilization monitor
#
# Build profile: stock Makefile with the upstream-recommended PREFIX
# override. amdgpu support is autodetected ON via libdrm_amdgpu probe;
# we have libdrm 2.4.123 in tree so the autodetect picks it up.
#
# Flags chosen:
#   PREFIX=/usr      standard distro install prefix
#   amdgpu=1         explicit-enable amdgpu code path (autodetect would
#                    pick it up anyway, but explicit is reproducible)
#   xcb=1            unprivileged Xorg monitoring (default; explicit)
#   nls=1            NLS translations (default; explicit)
#
# Cross-distro flag comparison:
#   Arch:   make PREFIX=/usr amdgpu=1 (xcb + nls default on)
#   Fedora: make PREFIX=/usr (defaults)
#   Debian: make PREFIX=/usr (defaults)
# We align exactly with Arch's explicit-amdgpu pattern.
#
# Security-only-alignment filter: TUI utility, no SUID, no daemon, no network
# surface. Reads performance counters via /dev/dri/card0 (which requires
# the running user to be in the `video` group or to run as root). No
# write surface; read-only sysfs.
#
# Install layout: Makefile installs to $(PREFIX)/sbin/radeontop +
# $(PREFIX)/share/man/man1/radeontop.1 + translations under
# $(PREFIX)/share/locale/. The /usr/sbin/radeontop path is upstream's
# choice; cross-distro convention.

configure() {
    set -e
    : # no-op (stock Makefile, no configure step)
}

build() {
    set -e
    make PREFIX=/usr amdgpu=1 xcb=1 nls=1 -j${IGOS_JOBS}
}

do_install() {
    set -e
    make PREFIX=/usr DESTDIR="$DESTDIR" install
}
