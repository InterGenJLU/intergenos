#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# virt-manager 5.1.0 — desktop VM manager (QEMU/KVM)
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# The graphical management front-end plus the virtinst CLI family
# (virt-install, virt-clone, virt-xml). Pure-python app driven through
# GObject introspection: gtk3, gtk-vnc, spice-gtk, vte, libvirt-glib,
# and libosinfo typelibs are the runtime surface, with libvirt-python
# and python-requests underneath (install-media fetching) and xorriso
# for unattended-install media injection. Default console graphics =
# SPICE. Icon-cache and gsettings-schema compilation are left to the
# image build (the squashfs customize hooks own those databases).
# NOTE: the optional XML-editor syntax highlighting wants GtkSource
# 3/4; the distribution ships gtksourceview5, so virt-manager falls
# back (by upstream design, logged at debug) to plain-text XML editing.

configure() {
    set -e
    meson setup build \
        --prefix=/usr \
        --buildtype=release \
        -Ddefault-graphics=spice \
        -Dupdate-icon-cache=false \
        -Dcompile-schemas=false
}

build() {
    set -e
    ninja -C build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" ninja -C build install
}
