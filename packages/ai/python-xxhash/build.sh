#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# python-xxhash 3.8.1 — Python binding for xxHash
#
# The datasets package imports this module at load time
# (datasets/fingerprint.py line 12, reached from datasets/__init__.py), so
# datasets is unusable without it. The tier: core xxhash package provides only
# the C library and the xxhsum CLI, which no Python import can use.
#
# XXHASH_LINK_SO=1 makes upstream's setup.py link the extension against the
# system libxxhash and drop the xxHash sources vendored in the release archive.
# Without it the extension statically compiles its own 0.8.2 copy of the hash,
# which would put a second, package-graph-invisible implementation of xxHash on
# the system alongside the 0.8.3 the xxhash package installs.

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    XXHASH_LINK_SO=1 pip3 wheel \
        --no-build-isolation \
        --no-deps \
        --no-cache-dir \
        -w dist \
        $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps \
        --no-index \
        --no-user \
        --no-cache-dir \
        --find-links dist \
        --root="$DESTDIR" \
        xxhash
}
