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
    # The tag archive's autogen.sh hard-requires gnome-common's
    # gnome-autogen.sh, which this system does not ship. configure.ac uses
    # exactly two macros this system cannot expand, and dropping both changes
    # nothing that ships (proven end-to-end in the chroot 2026-08-20: the
    # daemon, unit, and bus policy all still land):
    #   - GNOME_COMPILE_WARNINGS: extra compiler-warning flags only;
    #     src/Makefile.am consumes them as $(WARN_CFLAGS), which expands
    #     empty when unset.
    #   - GTK_DOC_CHECK: documentation REGENERATION machinery, default-off
    #     even where gtk-doc exists; no Makefile.am references its
    #     ENABLE_GTK_DOC conditional, and gtk-doc.m4 is not on this system,
    #     so the unexpanded macro would otherwise reach configure verbatim
    #     and die as a shell syntax error.
    sed -i '/GNOME_COMPILE_WARNINGS/d;/GTK_DOC_CHECK/d' configure.ac
    # docs/Makefile.am still unconditionally includes the gtk-doc.make
    # automake glue (only gtkdocize provides it). Write the same minimal
    # file upstream's own bootstrap (gnome-autogen.sh) writes when gtk-doc
    # is absent: the two variables the include and the later `CLEANFILES +=`
    # consume. Doc regeneration stays off; nothing shipped changes.
    printf 'EXTRA_DIST =\nCLEANFILES =\n' > gtk-doc.make
    autoreconf -fi
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
