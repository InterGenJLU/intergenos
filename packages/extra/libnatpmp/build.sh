#!/bin/bash
# libnatpmp 20230423 — NAT-PMP client library + natpmpc utility
# Authored 2026-05-09 to provide the system-library dep that transmission's
# USE_SYSTEM_NATPMP=ON expects. Replaces the bundled-libs -I hack
# (Halt #33 in Build #6).
#
# Build system: Makefile only. The shipped CMakeLists.txt has no install()
# rule and only builds a static lib — useless for our needs. The Makefile
# builds both static and shared, the natpmpc CLI, and has a working install
# rule. No DESTDIR support; we set INSTALLPREFIX="$DESTDIR/usr" to redirect
# the install layout into the package's stage directory.

configure() {
    set -e
    # Nothing to configure — Makefile reads PREFIX/INSTALLPREFIX from env.
    :
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make INSTALLPREFIX="$DESTDIR/usr" install
    # Upstream Makefile sets HEADERS=natpmp.h, omitting natpmp_declspec.h
    # which natpmp.h #includes at line 52. API consumers (transmission's
    # libtransmission/port-forwarding-natpmp.cc, etc.) fail with
    # "fatal error: natpmp_declspec.h: No such file or directory" without
    # this. Install the missing header explicitly — surfaced 2026-05-13
    # in Build #9 r#52 transmission 4.1.1 halt.
    install -m 644 natpmp_declspec.h "$DESTDIR/usr/include/natpmp_declspec.h"
}
