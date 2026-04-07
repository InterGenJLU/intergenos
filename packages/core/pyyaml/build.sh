#!/bin/bash
# PyYAML 6.0.3
# InterGenOS Chapter 8 Package
#
# Installs PyYAML into the system Python so that igos-build
# (Python orchestrator) works without runtime bootstrapping.
#
# This is the core copy — builds pure Python (no C extension).
# The desktop tier rebuilds with libyaml/Cython for performance.

configure() {
    : # No configure step
}

build() {
    pip3 wheel \
        --no-build-isolation \
        --no-deps \
        --no-cache-dir \
        -w dist \
        $PWD
}

do_install() {
    pip3 install \
        --no-index \
        --no-user \
        --no-deps \
        --no-cache-dir \
        --find-links dist \
        --root="$DESTDIR" \
        PyYAML
}

check() {
    python3 -c "import yaml; print(f'PyYAML {yaml.__version__}')"
}
