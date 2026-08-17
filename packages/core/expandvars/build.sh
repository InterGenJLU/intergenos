#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# expandvars 1.1.2 — Unix-style variable expansion.
#
# Build dependency for frozenlist, propcache, and yarl: each ships an in-tree
# PEP 517 backend (packaging/pep517_backend) whose _cython_configuration module
# imports `expandvars` unconditionally at backend-import time. Under our offline
# --no-build-isolation builds the backend's requires are NOT auto-installed, so
# without expandsvars present pip fails with
# "BackendUnavailable: Cannot import 'pep517_backend.hooks'". Packaged here
# ahead of those three consumers.
#
# Pure-Python single module; build-backend = hatchling.build (already present
# in the chroot).

configure() { : ; }

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    pip3 install --ignore-installed --no-deps --no-index --find-links dist --no-cache-dir --no-user --root="$DESTDIR" expandvars
}
