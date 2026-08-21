#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# nodejs 22.22.0 — JavaScript runtime built on V8
# BLFS 13.0

configure() {
    set -e
    # Patch applied by builder PATCH phase (package.yml) with SHA256 validation.

    # Use system libraries instead of bundled copies
    ./configure --prefix=/usr          \
                --shared-brotli        \
                --shared-cares         \
                --shared-libuv         \
                --shared-openssl       \
                --shared-nghttp2       \
                --shared-zlib          \
                --with-intl=system-icu
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    # The blanket suite mask was retired here 2026-08-19, along with the
    # failure count the comment beside it carried: that figure did not match
    # the reference book, which states 3 of over 4400 in the parallel suite.
    #
    # Corrected 2026-08-21 — `make test-only` never reached a test. It runs
    # build-addons first, whose stamp file test/addons/.buildstamp depends on
    # test/addons/.docbuildstamp, which in turn depends on the target
    # tools/doc/node_modules — and that target's recipe is `cd tools/doc &&
    # npm ci` (Makefile lines 383, 433-435 and 797-802 of the pinned
    # node-v22.22.0 tarball). The build chroot is offline, so npm ci fails and
    # make aborts before any test runs.
    #
    # The suite is invoked directly instead, over the set upstream itself
    # designates for an offline default run: tools/test.py's ArgsToTestPaths
    # expands `default` to every suite except IGNORED_SUITES, whose own
    # comment says those "represent special cases that should not be run as
    # part of the default JavaScript test-run, e.g., internet/ requires a
    # network connection, addons/ requires compilation" — so addons,
    # benchmark, doctool, embedding, internet, js-native-api, node-api and
    # pummel are excluded and no npm, no addon compilation and no network are
    # involved. The remaining suites are the JavaScript tests this package
    # ships a runtime for, and they are what the reference book's known-failure
    # count describes.
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        python3 tools/test.py --mode=release default
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Doc symlink ships as owned payload (hook-contract wave).
    install -dm755 "${DESTDIR}/usr/share/doc"
    ln -sfn node "${DESTDIR}/usr/share/doc/node-${PKG_VERSION}"
}

