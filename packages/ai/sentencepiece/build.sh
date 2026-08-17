#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# sentencepiece 0.2.2 — Python bindings + C++ core. The sdist bundles
# protobuf-lite/darts_clone/esaxx but NOT abseil (burn-proven 2026-07-22:
# upstream FetchContent-clones it) — the staged sha-pinned tarball rides in
# via abseil-offline-url.patch + SPM_ABSEIL_TARBALL, hash-verified by the
# populate step itself.
#
# env -u DESTDIR on the wheel build (DESTDIR-redirect class, third victim
# preempted): setup.py's build_bundled.sh runs `cmake --target install` into
# ./build/root, and cmake's install honors an inherited DESTDIR — the core
# libs would land in the package staging dir and the extension link would
# find an empty build/root/lib. do_install passes DESTDIR inline, unaffected.
#
# The wheel ships only the python package — the spm_* CLI tools stop at
# ./build/root/bin — so do_install stages them explicitly (fail-loud install;
# the verify_paths /usr/bin/spm_train pin proves the leg at seal time).

configure() {
    set -e
    # Offline-abseil lever — fails LOUD (patch reject) if upstream reworked
    # the FetchContent block.
    BUILD_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
    patch -Np1 -i "$BUILD_DIR/abseil-offline-url.patch"
}

build() {
    set -e
    export SPM_ABSEIL_TARBALL="${IGOS_SOURCES}/abseil-cpp-20260526.0.tar.gz"
    [ -f "${SPM_ABSEIL_TARBALL}" ] || { echo "FATAL: staged abseil tarball missing: ${SPM_ABSEIL_TARBALL}"; exit 1; }
    env -u DESTDIR pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" sentencepiece
    # The spm_* CLI tools from the core build — the wheel does not carry them.
    for tool in spm_train spm_encode spm_decode spm_normalize spm_export_vocab; do
        install -Dm755 "build/root/bin/${tool}" "${DESTDIR}/usr/bin/${tool}"
    done
}
