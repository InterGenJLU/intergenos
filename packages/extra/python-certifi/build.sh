#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# python-certifi 2026.6.17 — TLS trust anchor shim (SYSTEM CA bundle)
# Not in BLFS — InterGenOS extra tier (virtualization stack support)
#
# Runtime dependency of python-requests (virt-manager install-media
# fetching). Upstream certifi bundles its own Mozilla cacert.pem — a
# SECOND trust root that bypasses the system CA store. That is not
# acceptable here: the system has exactly ONE trust root, the make-ca
# bundle at /etc/ssl/certs/ca-certificates.crt (the squashfs build
# gate fails if it is missing). This package therefore replaces
# certifi/core.py so where()/contents() serve the SYSTEM bundle, and
# does not install the bundled cacert.pem at all (the Debian/Gentoo
# system-store posture).

SYSTEM_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

configure() {
    set -e
    # Replace the upstream importlib.resources loader with the system-
    # bundle shim BEFORE the wheel is built, so the wheel itself is the
    # patched artifact.
    cat > certifi/core.py <<PYEOF
"""
certifi.core — InterGenOS system-trust shim.

Upstream certifi extracts a bundled cacert.pem via importlib.resources.
InterGenOS carries exactly one trust root: the make-ca system bundle.
This module serves that bundle instead. (Downstream replacement, same
public API: where() and contents().)
"""

_SYSTEM_CA_BUNDLE = "${SYSTEM_CA_BUNDLE}"


def where() -> str:
    return _SYSTEM_CA_BUNDLE


def contents() -> str:
    with open(where(), encoding="ascii", errors="replace") as f:
        return f.read()
PYEOF
    rm -f certifi/cacert.pem
    # Drop the pem from the sdist's install manifest so setuptools does
    # not fail on the removed file.
    sed -i '/cacert\.pem/d' MANIFEST.in setup.py 2>/dev/null || true
    sed -i 's/"cacert\.pem", //; s/, "cacert\.pem"//; s/"cacert\.pem"//' setup.py setup.cfg pyproject.toml 2>/dev/null || true
}

build() {
    set -e
    pip3 wheel --no-build-isolation --no-deps --no-cache-dir -w dist "$PWD"
}

do_install() {
    set -e
    pip3 install --no-index --no-user --no-deps --no-cache-dir --ignore-installed \
        --find-links dist --root="$DESTDIR" certifi
    # Fail loudly if the bundled pem leaked into the install anyway.
    if find "$DESTDIR" -name cacert.pem | grep -q .; then
        echo "FATAL: bundled cacert.pem shipped — system-trust shim violated" >&2
        exit 1
    fi
}
