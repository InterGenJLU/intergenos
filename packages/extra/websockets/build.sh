#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# websockets 16.0 — WebSocket RFC 6455/7692, pure-Python, zero PyPI exposure.
#
# WHY: WebSocket transport for InterGen web_server.py and console/client.py.
# Replaces aiohttp + 6 C-extension transitive deps per the decision Path B
# (2026-05-28). This package is the transport layer; the HTTP-static-file
# surface uses stdlib http.server per the locked HTTP-server-replacement
# decision (pattern doc §7).
#
# SOURCE: python-websockets/websockets on GitHub, v16.0 tagged 2026-01-10
# (138 days old; satisfies the 90-day floor per pattern doc §2.2).
#
# TAG SIGNING: v16.0 is a lightweight tag (NOT GPG-signed). Per pattern doc
# §2.4 minimum-witness shape for unsigned packages: (1) curl+sha256 local
# fetch + (3) Debian sid python-websockets 16.0-1 source mirror.
# Witness (1): sha256 c720700d313af5d1c61372da62d78c5b6192ed09871367df65a8e1087b60b1f0
# Witness (3): Debian sid source package python-websockets 16.0-1
#   (https://packages.debian.org/source/sid/python-websockets)
# Absence of witness (2) called out per §2.4: maintainer does not sign tags.
#
# C EXTENSION: setup.py:20-38 ships optional speedups.c BUILD_EXTENSION env-var
# gate. BUILD_EXTENSION=no forces pure-Python fallback per locked
# decision 2026-05-28. Eliminates ~100-line C trust-root. Runtime UTF-8
# validation cost is negligible at our load profile.
#
# BUILD-HOOK AUDIT (pattern doc §2.5):
# (1) setup.py install_requires: empty (stdlib-only; no runtime deps)
# (2) [build-system] requires: setuptools only (we ship)
# (3) ext_modules: present per §2.7, handled via BUILD_EXTENSION=no above
# (4) Build hooks: no network fetches; no suspicious imports; no cmdclass overrides
# (5) CI: GitHub actions present (posix + windows matrix); release discipline observable
#
# PyPI SOURCE BYPASS per pattern doc §1: Mini Shai-Hulud (2026-05-12) +
# Waves 1-4 + TanStack SLSA-3 defeat + decided indefinite prohibition
# (2026-05-27). This recipe sources from GitHub directly with zero PyPI hops.
#
# PATTERN DOC: docs/operations/pure-python-github-source-pattern.md
# PRECEDENT: packages/core/maturin/ (Rust+Python; .dist-info minting shape)

configure() { : ; }

build() {
    set -e
    export BUILD_EXTENSION=no
    export SOURCE_DATE_EPOCH=1768036088
    pip3 wheel \
        --wheel-dir dist \
        --isolated \
        --no-build-isolation \
        --no-deps \
        --no-index \
        --no-cache-dir \
        --verbose \
        .
}

do_install() {
    set -e
    python_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    site_packages="${DESTDIR}/usr/lib/python${python_version}/site-packages"

    # Install wheel — pip creates correct .dist-info with complete RECORD
    pip3 install --ignore-installed --no-deps \
        --no-index \
        --find-links dist \
        --no-cache-dir \
        --no-user \
        --root="$DESTDIR" \
        --ignore-installed \
        websockets

    echo "websockets 16.0 installed (pure-Python; BUILD_EXTENSION=no)"
}

check() {
    set -e
    # CHECK runs BEFORE INSTALL in the extra-tier flow, so the just-built
    # websockets is not yet on the system python — a live `import websockets`
    # here always fails with ModuleNotFoundError (this halted the build on
    # 2026-06-02). Same misordered-check class as pyyaml (fixed 2026-06-01).
    # The functional import smoke-test lives in post_install() (post-deploy),
    # where it is meaningful. Here, verify build() actually produced the wheel.
    ls dist/websockets-*.whl >/dev/null
}

post_install() {
    set -e
    # Live-system verification: websockets is now deployed, so it MUST import
    # and report a version. A failure here is real and fatal (set -e halts the
    # build — no masking).
    python3 -c "import websockets; print(f'websockets {websockets.__version__} — import OK')"
}