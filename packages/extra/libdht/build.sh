#!/bin/bash
# libdht 0.27 — BitTorrent Mainline DHT client library (jech/dht)
#
# System-library wrapper for jech/dht. transmission's FindDHT.cmake expects:
#   pkg_check_modules(_DHT QUIET libdht)            -> libdht.pc
#   find_path(... NAMES dht/dht.h)                  -> /usr/include/dht/dht.h
#   find_library(... NAMES dht)                     -> /usr/lib/libdht.{so,a}
#
# Upstream's Makefile only builds the dht-example test client — no install,
# no shared library, no static library, no pkg-config. We compose the install
# ourselves: shared lib, static lib, header at the dht/ subpath transmission
# expects, and a generated libdht.pc.
#
# Source pinned to master HEAD commit 0bbb8f4a (latest stability fixes since
# the 0.27 release April 2022; library is very stable so HEAD = 0.27 + minor
# patches). License: MIT (Juliusz Chroboczek).
#
# Added 2026-05-13 to unbundle DHT from transmission's CMake (Build #9 r#53
# transmission halt: "No rule to make target third-party/dht.bld/pfx/lib/
# libdht.a"). Same maintainer-approved pattern that resolved libnatpmp /
# miniupnpc / libdeflate on 2026-05-09 — system libs instead of bundled
# ExternalProject_Add.

configure() {
    set -e
    # Nothing to configure — bespoke Makefile-free build below.
    :
}

build() {
    set -e
    # Compile dht.c with -fPIC for shared + static.
    # CFLAGS inherited from environment (-march/-mtune/-O2/etc).
    gcc ${CFLAGS} -fPIC -Wall -c dht.c -o dht.o

    # Static archive.
    ar rcs libdht.a dht.o

    # Shared library with SONAME. dht.c links against -lcrypt (per upstream
    # Makefile: LDLIBS = -lcrypt). We carry that through.
    gcc -shared -Wl,-soname,libdht.so.0 -o libdht.so.0.27 dht.o -lcrypt
}

do_install() {
    set -e
    # Header at /usr/include/dht/dht.h (note: nested subdir, NOT /usr/include/
    # directly — that's what transmission's FindDHT.cmake searches for).
    install -d -m 755 "$DESTDIR/usr/include/dht"
    install -m 644 dht.h "$DESTDIR/usr/include/dht/dht.h"

    # Libraries + SONAME symlinks.
    install -d -m 755 "$DESTDIR/usr/lib"
    install -m 644 libdht.a "$DESTDIR/usr/lib/libdht.a"
    install -m 755 libdht.so.0.27 "$DESTDIR/usr/lib/libdht.so.0.27"
    ln -sfv libdht.so.0.27 "$DESTDIR/usr/lib/libdht.so.0"
    ln -sfv libdht.so.0    "$DESTDIR/usr/lib/libdht.so"

    # pkg-config descriptor — what FindDHT.cmake's pkg_check_modules(libdht)
    # discovers. Note Requires/Cflags/Libs minimal — dht.h has no transitive
    # header deps; libdht.so needs -lcrypt at link time (declared in Libs).
    install -d -m 755 "$DESTDIR/usr/lib/pkgconfig"
    cat > "$DESTDIR/usr/lib/pkgconfig/libdht.pc" <<'PCEOF'
prefix=/usr
exec_prefix=${prefix}
libdir=${exec_prefix}/lib
includedir=${prefix}/include

Name: libdht
Description: BitTorrent Mainline DHT client library (jech/dht)
Version: 0.27
Libs: -L${libdir} -ldht
Libs.private: -lcrypt
Cflags: -I${includedir}
PCEOF

    # License + docs for legal compliance + system inspection.
    install -d -m 755 "$DESTDIR/usr/share/doc/libdht-0.27"
    install -m 644 LICENCE  "$DESTDIR/usr/share/doc/libdht-0.27/LICENCE"
    install -m 644 README   "$DESTDIR/usr/share/doc/libdht-0.27/README"
    install -m 644 CHANGES  "$DESTDIR/usr/share/doc/libdht-0.27/CHANGES"
}
