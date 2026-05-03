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
    :
}

build() {
    # SUBDIRS override: skip apps/ which builds example EFI binaries
    # (HelloWorld.efi, t.efi, etc. — demos for using the library, not
    # consumed by efitools/sbsigntool/mokutil/shim-signed). On gnu-efi
    # 3.0.18 with binutils 2.46, objcopy fails with "file format not
    # recognized" on those .so→.efi conversions, while the library
    # outputs (libefi.a, libgnuefi.a, crt0-efi-x86_64.o) build cleanly.
    # Per feedback_dependency_policy.md: examples-only subset = SKIP.
    make -j${IGOS_JOBS} SUBDIRS="lib gnuefi inc"
}

check() {
    # gnu-efi has no test suite
    :
}

do_install() {
    # PREFIX = in-system prefix (/usr); LIBDIR = where libs land within
    # PREFIX. DESTDIR (exported by build framework as $PKG_DEST) is
    # picked up by INSTALLROOT in Make.defaults — do not duplicate it
    # in PREFIX. SUBDIRS override matches build() — skip apps/.
    make install PREFIX=/usr LIBDIR=/usr/lib SUBDIRS="lib gnuefi inc"
}
