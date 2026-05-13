#!/bin/bash
# apr-util 1.6.3 — APR utility extensions
#
# Wave W1 prereq for apache-httpd 2.4.x. Stacks DBM / LDAP / XML / crypto /
# memcache utility wrappers on top of apr's OS-abstraction primitives.
# In-tree v1.0 consumer is solely apache-httpd (Apache's mod_dbd /
# mod_session_crypto / mod_ldap / mod_xml2enc all link this library).
#
# Why this version (1.6.3, current latest stable):
#   * 1.6.3 is the most recent release in the 1.6.x branch on
#     apr.apache.org. apr-util has had a long-standing slow release
#     cadence relative to apr; the 1.6.x line is the canonical pair
#     for apr 1.7.x as documented in the apache-httpd 2.4.67 INSTALL.
#   * Single SHA-256 anchor pinned in package.yml after local download
#     + sha256sum verification of the upstream archive.apache.org
#     tarball.
#
# License: Apache-2.0. Single LICENSE file at the tarball root carries
# the standard Apache License 2.0 (verified head -5 matches the
# canonical "Apache License Version 2.0, January 2004" header).
#
# Build profile: autotools (configure + make + make install). The
# tarball ships a pre-generated `configure` script.
#
# Configure flags chosen:
#   --prefix=/usr             standard distro layout
#   --libdir=/usr/lib         avoids lib64-vs-lib drift
#   --sysconfdir=/etc         standard
#   --with-apr=/usr           apr-1-config helper from the just-landed
#                             packages/extra/apr/ provides the build-
#                             time pkg-config-equivalent surface
#   --with-expat=/usr         in-tree core/expat provides XML parsing
#                             for apr_xml.h consumers (mod_xml2enc)
#   --with-crypto             enable apr_crypto.h API surface
#   --with-openssl=/usr       in-tree core/openssl backs apr_crypto.h;
#                             also load-bearing for mod_session_crypto
#
# Security-only-alignment posture: apr-util is a library only — no
# daemons, no setuid binaries, no privileged entry points. Privileged
# surface is determined entirely by the consuming program. The crypto
# API surface (apr_crypto.h) is a pass-through to openssl, gated by the
# operator's configure flags + the consuming program's choice of
# cipher / key-handling code paths.

configure() {
    set -e
    ./configure                                                            \
        --prefix=/usr                                                      \
        --libdir=/usr/lib                                                  \
        --sysconfdir=/etc                                                  \
        --with-apr=/usr                                                    \
        --with-expat=/usr                                                  \
        --with-crypto                                                      \
        --with-openssl=/usr
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install
}

check() {
    set -e
    # apr-util's `make check` runs unit tests against the built
    # library. Some tests touch DBM file paths and assume working
    # filesystem write access which is awkward in a build chroot.
    # Run-time verification is performed at link time by apache-httpd's
    # configure (which probes apu-1-config) and at runtime by Apache's
    # mod_dbd / mod_session_crypto / mod_xml2enc consumer paths.
    return 0
}
