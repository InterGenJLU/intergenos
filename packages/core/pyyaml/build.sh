#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# PyYAML 6.0.3
# InterGenOS Chapter 8 Package
#
# Installs PyYAML into the system Python so that igos-build
# (Python orchestrator) works without runtime bootstrapping.
#
# This is the core copy — builds pure Python (no C extension).
# The desktop tier rebuilds with libyaml/Cython for performance.

configure() {
    set -e
    : # No configure step
}

build() {
    set -e
    pip3 wheel \
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
        --no-deps \
        --no-cache-dir \
        --find-links dist \
        --root="$DESTDIR" \
        PyYAML
}

check() {
    set -e
    # In the Ch8 flow CHECK runs BEFORE INSTALL, so the just-built PyYAML is not
    # yet on the system python — `import yaml` would resolve to an unrelated/
    # uninstalled module (this is exactly what produced the spurious
    # "module 'yaml' has no attribute '__version__'" failure on 2026-06-01).
    # The functional import test therefore lives in post_install() (post-deploy),
    # where it is meaningful. Here, verify build() actually produced the wheel.
    ls dist/pyyaml-*.whl >/dev/null
}

post_install() {
    set -e
    # Live-system verification: PyYAML is now deployed, so it MUST import and
    # report a version. igos-build's Python orchestrator depends on it, so a
    # failure here is real and fatal (set -e halts the build — no masking).
    python3 -c "import yaml; print(f'PyYAML {yaml.__version__} — import OK')"
}
