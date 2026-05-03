#!/bin/bash
# tdb 1.4.15 — Trivial Database (Samba's small, fast key/value store)
# BLFS 13.0 (Samba family — referenced from samba's tdb dep)
#
# Why we ship tdb as its own package:
#   Rhythmbox links libtdb for its on-disk metadata cache. Other Samba-stack
#   consumers (talloc/tevent/ldb/samba) also depend on it; carving tdb out
#   keeps the dependency graph honest rather than vendoring a copy inside
#   each consumer.
#
# Build system:
#   tdb uses Samba's waf build system (Python). The shipped ./configure is a
#   thin wrapper that invokes `python waf configure ...`, then `make` and
#   `make install` likewise wrap waf. DESTDIR is honored via the env var on
#   `make install`, matching how the samba package installs.
#
# Flags:
#   --prefix=/usr           — system install prefix
#   --disable-rpath-install — no embedded rpath in installed libs (BLFS-style;
#                             matches our samba package).
#   Python bindings are built by default (waf --disable-python would skip
#   them, but Rhythmbox-side scripting and downstream consumers benefit from
#   pytdb being available, and Python is already a build dep).

configure() {
    ./configure                    \
        --prefix=/usr              \
        --disable-rpath-install
}

build() {
    make -j${IGOS_JOBS}
}

# Upstream test target runs the tdb selftest under waf. Tests are local to
# the build tree and do not require network. "tests are truth" — failures
# fail the build.
check() {
    make test
}

do_install() {
    make DESTDIR="$DESTDIR" install
}
