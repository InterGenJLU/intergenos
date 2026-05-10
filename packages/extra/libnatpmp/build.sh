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
}
