#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# switcheroo-control 1.3.1 — the small D-Bus system service that tells the
# desktop which graphics processor an application should be launched on.
#
# Build system verified against the pinned tarball:
#   - autotools, and the GitHub tag archive ships configure.ac WITHOUT a
#     generated ./configure (only autogen.sh), so autoreconf runs here. That is
#     why autoconf/automake/libtool are build dependencies for this recipe and
#     not for the release-tarball packages in this wave.
#   - One library dependency: PKG_CHECK_MODULES(SWITCHEROO_CONTROL, gio-2.0)
#     (configure.ac:33). glib-compile-resources is also needed at build time to
#     turn switcheroo-control.gresource.xml into C; both come from glib2.
#   - The program installs into libexecdir (src/Makefile.am: libexec_PROGRAMS),
#     not bindir — it is started by systemd, never typed by a user.
#   - data/Makefile.am installs the systemd unit into systemdsystemunitdir and
#     the bus policy into $(sysconfdir)/dbus-1/system.d, so both directories are
#     passed explicitly below rather than left to configure's guess.
#
# The unit is Type=dbus with BusName=net.hadess.SwitcherooControl and
# WantedBy=graphical.target. On this system the install-time `systemctl
# preset-all` disables anything not whitelisted, so the unit is listed in
# intergenos-base-files' 80-intergenos-enable.preset; without that line the
# service would ship present and never start, and the desktop's discrete-GPU
# menu entry would be silently missing.

configure() {
    set -e
    NOCONFIGURE=1 ./autogen.sh
    ./configure                                                   \
        --prefix=/usr                                             \
        --libexecdir=/usr/libexec                                  \
        --sysconfdir=/etc                                          \
        --mandir=/usr/share/man                                    \
        --with-systemdsystemunitdir=/usr/lib/systemd/system        \
        --with-udevrulesdir=/usr/lib/udev/rules.d
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}
