#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# pyarrow 25.0.0 — bindings against the SYSTEM arrow-cpp (never the bundled
# build: PYARROW_BUILD_TYPE + the scikit-build-core cmake discover libarrow
# via the installed ArrowConfig.cmake). The dataset/parquet python modules
# require the matching arrow-cpp features (ON in that recipe).

configure() {
    set -e
    :
}

build() {
    set -e
    export PYARROW_WITH_DATASET=1
    export PYARROW_WITH_PARQUET=1
    # env -u DESTDIR (DESTDIR-redirect class, FIFTH strike — the first SILENT
    # one): scikit-build-core's internal cmake-install honored the exported
    # DESTDIR and delivered every compiled extension into
    # $DESTDIR/tmp/<wheeltmp>/wheel/platlib/ — the wheel packed source files
    # only, the seal gate passed on pure-python verify_paths, and the sealed
    # archive shipped an import-broken pyarrow plus 401 stray ./tmp paths
    # (burn-caught 2026-07-22 by the archive stray-path scan). The
    # verify_paths native-lib pins in package.yml make the slim-wheel shape
    # unsealable from now on.
    env -u DESTDIR pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir "$PWD"
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-user \
         --root="$DESTDIR" pyarrow
}
