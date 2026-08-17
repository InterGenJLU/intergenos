#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# libdbusmenu 16.04.0 — Library for passing GTK menus over D-Bus.
#
# Provides the Dbusmenu GObject-Introspection typelibs
#   /usr/lib/girepository-1.0/Dbusmenu-0.4.typelib
#   /usr/lib/girepository-1.0/DbusmenuGtk3-0.4.typelib
# that GNOME Shell's appindicatorsupport + dash-to-dock require at runtime for
# system-tray menus and dash quicklists. Without them gnome-shell logs (seen on
# the GBC003.1 .192 install):
#   "Failed to import DBusMenu, quicklists are not available: Requiring Dbusmenu,
#    version none: Typelib file for namespace 'Dbusmenu' (any version) not found".
#
# Last upstream release (2016) but universally shipped (Arch/Debian/Fedora/
# openSUSE/Alpine/Gentoo). Built from the canonical launchpad release tarball —
# same artifact those distros ship (sha512 matches Arch's pin). The release
# tarball ships a pre-generated ./configure, so no autogen/gnome-common needed.
#
# build_style: custom (not autotools) because we need the -Werror sed BEFORE
# configure and a .la cleanup BEFORE install — neither expressible via the
# autotools style's configure_flags (same reason libcanberra hand-rolls).

configure() {
    set -e
    # Recipe aligned to the DEBIAN libdbusmenu packaging (debian/rules): a single
    # gtk3 build tree, tests left ENABLED, serial install. (Gentoo's alternative
    # — patch configure.ac's HAVE_VALGRIND + autoreconf + separate per-GTK build
    # dirs — needs gnome-common to autoreconf, which this chroot does not ship;
    # Debian's form avoids autoreconf entirely and is equally distro-proven.)
    #
    # GCC-15 fix (Gentoo libdbusmenu-16.04.0-werror.patch): the source hard-codes
    # -Werror, and modern glib/gtk3 deprecation warnings turn that fatal. Strip
    # -Werror from every Makefile.in (what configure consumes — no autoreconf
    # here) so the lib AND the (enabled) test/tool sources compile.
    find . -name 'Makefile.in' -exec sed -i 's/-Werror//g' {} +
    find . -name 'Makefile.am' -exec sed -i 's/-Werror//g' {} + 2>/dev/null || true
    # NOTE: tests stay ENABLED on purpose. libdbusmenu emits the AM_CONDITIONAL
    # [HAVE_VALGRIND] ONLY inside configure's `enable_tests != no` block, but
    # tests/Makefile.am references it unconditionally — so `--disable-tests`
    # makes config.status abort with `conditional "HAVE_VALGRIND" was never
    # defined`. With tests enabled the conditional is defined (=no, no valgrind
    # pkg), and the test sources compile against json-glib (in-tree, core). We
    # never run `make check` (that needs dbus-test-runner, not shipped), so it is
    # harmless. This is exactly what debian/rules does.

    ./configure --prefix=/usr           \
                --sysconfdir=/etc       \
                --localstatedir=/var    \
                --disable-static        \
                --disable-dumper        \
                --disable-gtk-doc       \
                --enable-introspection  \
                --with-gtk=3
    # --enable-introspection (not the default "auto"): fail loud if
    #   gobject-introspection is missing rather than silently shipping NO
    #   typelib — the typelib IS the point of this package.
    # NOTE: do NOT pass --disable-tests. libdbusmenu defines the HAVE_VALGRIND
    #   automake conditional ONLY inside the `enable_tests != no` block of
    #   configure, but tests/Makefile.am references HAVE_VALGRIND
    #   unconditionally — so --disable-tests makes config.status abort with
    #   `conditional "HAVE_VALGRIND" was never defined`. Building with tests
    #   enabled defines it (=no, since no valgrind pkg is present) and the test
    #   programs compile against json-glib (in-tree, core). We never run
    #   `make check` (that needs dbus-test-runner, not shipped), so the absent
    #   test-runner is irrelevant. This is how Debian/Ubuntu build it.
    # --disable-dumper: drops the dbusmenu-dumper debug tool.
    # --disable-gtk-doc: skip the gtk-doc/scrollkeeper apidoc path.
    # --with-gtk=3: builds libdbusmenu-gtk3 + DbusmenuGtk3-0.4.typelib (VER=3).
}

build() {
    set -e
    make -j"${IGOS_JOBS}"
}

do_install() {
    set -e
    # Drop .la files before install — libtool relink chokes on GCC 15 when
    # relinking the .so during the DESTDIR install (same class as libcanberra).
    find . -name "*.la" -delete
    # -j1: libdbusmenu's gtk + gtk3 header-install targets both write the SAME
    # headers to .../libdbusmenu-gtk3-0.4/libdbusmenu-gtk/, so a parallel
    # `make install` races and aborts with "cannot create regular file …:
    # File exists" (build-rules §2.10 parallel-unsafe install class). Serialize.
    make -j1 DESTDIR="${DESTDIR}" install
}

post_install() {
    set -e
    ldconfig
}
