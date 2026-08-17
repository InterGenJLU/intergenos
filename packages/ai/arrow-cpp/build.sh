#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# arrow-cpp 25.0.0 — the Arrow C++ platform for pyarrow/datasets (training stack).
# Feature set = what the datasets/pandas training path consumes: compute,
# csv, json, filesystem, dataset, parquet + the compression codecs.
#
# OFFLINE DISCIPLINE (the load-bearing flag): ARROW_DEPENDENCY_SOURCE=SYSTEM.
# Arrow's cmake otherwise FetchContent-downloads missing third-party deps at
# configure time — with SYSTEM, every dep must resolve from the chroot
# (declared in package.yml) and a miss fails loudly at configure, never a
# silent network fetch.
#
# ONE exception SYSTEM does not cover: mimalloc. Upstream force-bundles it
# (vendored-only by design, ThirdpartyToolchain.cmake:2543) and downloads it at
# BUILD time via ExternalProject. The staged tarball (declared in package.yml)
# is handed over through ARROW_MIMALLOC_URL; arrow still verifies it against
# its own URL_HASH sha256 pin, and a missing/mismatched file fails loudly.
#
# env -u DESTDIR on configure+build: the DESTDIR-redirect class — the builder
# exports DESTDIR for the whole package run, and the mimalloc_ep ExternalProject
# INSTALL step (a nested `cmake --install` running at ninja time) honors it,
# silently installing the ep payload into the package staging dir instead of
# arrow's MIMALLOC_PREFIX (headers then missing at compile). Same mechanism as
# the pybind11/scikit-build-core wheel redirect. do_install passes DESTDIR
# inline, so stripping it from the build env changes nothing for staging.

configure() {
    set -e
    export ARROW_MIMALLOC_URL="${IGOS_SOURCES}/mimalloc-v3.3.1.tar.gz"
    [ -f "${ARROW_MIMALLOC_URL}" ] || { echo "FATAL: staged mimalloc tarball missing: ${ARROW_MIMALLOC_URL}"; exit 1; }
    env -u DESTDIR cmake -S cpp -B build -G Ninja \
          -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release \
          -DARROW_DEPENDENCY_SOURCE=SYSTEM \
          -DARROW_BUILD_STATIC=OFF \
          -DARROW_COMPUTE=ON \
          -DARROW_CSV=ON \
          -DARROW_JSON=ON \
          -DARROW_FILESYSTEM=ON \
          -DARROW_DATASET=ON \
          -DARROW_PARQUET=ON \
          -DARROW_WITH_SNAPPY=ON \
          -DARROW_WITH_BROTLI=ON \
          -DARROW_WITH_LZ4=ON \
          -DARROW_WITH_ZSTD=ON \
          -DARROW_WITH_ZLIB=ON \
          -DARROW_BUILD_TESTS=OFF
}

build() {
    set -e
    env -u DESTDIR cmake --build build
}

do_install() {
    set -e
    DESTDIR="$DESTDIR" cmake --install build
}
