#!/bin/bash
# gnu-efi 3.0.18 — UEFI development headers and libraries
# Required by efitools, sbsigntool, and shim for EFI binary builds
# Upstream: https://sourceforge.net/projects/gnu-efi/

configure() {
    # gnu-efi uses a plain Makefile, no autotools. Make.defaults sets
    # reasonable defaults for x86_64 detection. INSTALLROOT defaults to
    # $(DESTDIR), which the build framework already exports — so we let
    # the env value drive staging instead of overriding PREFIX with a
    # DESTDIR-prefixed path (which doubles the staging path).
    #
    # Skip apps/ at file level: apps/ contains example EFI binaries
    # (HelloWorld.efi, t.efi, etc.) — demos, not features consumed by
    # efitools/sbsigntool/mokutil/shim-signed. On gnu-efi 3.0.18 +
    # binutils 2.46, objcopy fails "file format not recognized" on the
    # .so→.efi conversions in apps/, while lib/, gnuefi/, inc/ build
    # cleanly. Per the project's dependency-enablement policy: examples-only = SKIP.
    #
    # We patch the Makefile rather than passing SUBDIRS= on the make
    # command line: command-line make variables propagate to all
    # sub-makes, and lib/Makefile defines its own SUBDIRS (per-arch
    # build-tree dir list including runtime/, x86_64/). An overriding
    # top-level SUBDIRS= clobbers lib's, breaking its libsubdirs
    # mkdir-p step and yielding "can't create runtime/X.o: No such
    # file or directory" failures.
    sed -i 's/^SUBDIRS = lib gnuefi inc apps$/SUBDIRS = lib gnuefi inc/' Makefile
}

build() {
    make -j${IGOS_JOBS}
}

check() {
    # gnu-efi has no test suite
    :
}

do_install() {
    # PREFIX = in-system prefix (/usr); LIBDIR = where libs land within
    # PREFIX. DESTDIR (exported by build framework as $PKG_DEST) is
    # picked up by INSTALLROOT in Make.defaults — do not duplicate it
    # in PREFIX. SUBDIRS override happens in configure() via sed-patch,
    # so make install iterates only lib/gnuefi/inc.
    make install PREFIX=/usr LIBDIR=/usr/lib
}
