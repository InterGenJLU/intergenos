#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# gparted 1.8.1 — GNOME Partition Editor (GTK3 / gtkmm-3.0 >= 3.18).
# GUI front-end over libparted (in-tree parted); shells out to the
# mkfs.*/fsck.* tools (dosfstools, e2fsprogs, ntfs-3g, exfatprogs) at runtime
# and runs privileged via polkit (org.gnome.gparted.policy). The wrapper script
# lands at /usr/bin/gparted; the real binary at /usr/libexec/gpartedbin.
# --enable-libparted-dmraid uses parted's libdevmapper (/dev/mapper) support.
# Upstream ships a pre-generated configure (autotools dist tarball); help docs
# build via itstool (in-tree).

configure() {
    set -e
    ./configure --prefix=/usr             \
                --enable-libparted-dmraid \
                --disable-static
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
