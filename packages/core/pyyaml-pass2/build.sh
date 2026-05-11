#!/bin/bash
# PyYAML 6.0.3 — Pass 2 rebuild with Cython/libyaml C extension
#
# Pass 1 (core) installs pure-Python PyYAML for igos-build.
# This pass rebuilds with libyaml C bindings for performance.

configure() {
    set -e
    :
}

build() {
    set -e
    pip3 wheel -w dist --no-build-isolation --no-deps --no-cache-dir $PWD
}

do_install() {
    set -e
    # --force-reinstall: overwrite pure-Python pass 1 with C extension
    pip3 install --no-index --find-links dist --no-user \
         --no-deps --force-reinstall PyYAML
}

check() {
    set -e
    python3 -c "
import yaml
print(f'PyYAML {yaml.__version__}')
# Verify C extension loaded (libyaml)
if hasattr(yaml, 'CSafeLoader'):
    print('  C extension: YES (libyaml)')
else:
    print('  C extension: NO (pure Python)')
"
}
