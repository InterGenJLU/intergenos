#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
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
    # Stage into DESTDIR — this package is built by the bash builder
    # (scripts/chroot-build-core-extra.sh), whose pipeline is
    # stage -> manifest -> archive -> deploy: the ARCHIVE is a tar of the staging
    # tree, so anything written outside DESTDIR is deployed but never captured.
    # This previously installed straight to the live filesystem, which put the
    # C extension in the build chroot (and so onto the live image via squashfs)
    # while the archive carried nothing but the bundled LICENSE. Every install
    # from that archive therefore got pure-Python PyYAML, and this package's
    # own post_install C-extension assertion is what failed on the target.
    #
    # --ignore-installed is the flag that makes this work, and it is the same
    # correction pass 1 took at its r2: --root only relocates where files land,
    # while pip's already-satisfied check still consults the REAL environment —
    # and pass 1 has by definition already installed PyYAML there. Without it
    # pip reports "already satisfied", writes nothing into DESTDIR, and the
    # empty-archive class returns wearing a different flag.
    pip3 install --ignore-installed --no-index --find-links dist --no-user \
         --no-deps --no-cache-dir --root="$DESTDIR" PyYAML
}

check() {
    set -e
    # CHECK runs before INSTALL, so the just-built wheel is not yet deployed and
    # `import yaml` would resolve to the already-installed pass-1 (pure-Python)
    # module — testing the wrong artifact. The functional/C-extension test lives
    # in post_install() (post-deploy). Here, verify build() produced the wheel.
    ls dist/pyyaml-*.whl >/dev/null
}

post_install() {
    set -e
    # Live-system verification (post-deploy). pass2's PURPOSE is the libyaml C
    # extension; if it didn't load, the rebuild silently degraded to pure Python
    # — a real failure (HG: no silent degradation), so HALT. Also fatal if the
    # module won't import or has no version.
    python3 - <<'PY'
import sys, yaml
print(f'PyYAML {yaml.__version__} — import OK')
if hasattr(yaml, 'CSafeLoader'):
    print('  C extension: YES (libyaml)')
else:
    sys.exit('  C extension: NO — pass2 failed to deliver libyaml bindings (FATAL)')
PY
}
