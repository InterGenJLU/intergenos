#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# python-pip — the pip installer, owned as a package.
#
# Decided 2026-07-28 (punch-down A-3): pip previously entered the chroot
# only as an interpreter self-install, so the live image carried a pip no
# archive owned while installed systems had no pip at all. This package
# bootstraps pip from the interpreter's own bundled ensurepip wheel —
# no external source, the wheel is pinned by the python recipe's tarball
# — and stages it into DESTDIR so the payload is archived, manifest-
# owned, and installed by Forge like any other package.
#
# The interpreter's own install may also write pip live into the build
# chroot; those bytes come from the same bundled wheel, so any overlap
# with this package's deployment is byte-identical (the shared-ownership
# rule for identical bytes).

do_install() {
    set -e

    # The bundled wheel is versioned with the interpreter. Fail closed if
    # the recipe's declared version no longer matches, so a python upgrade
    # bumps this recipe in the same change instead of shipping a
    # mislabeled pip.
    local bundled
    bundled=$(python3 -c 'import ensurepip; print(ensurepip.version())')
    if [ "$bundled" != "$PKG_VERSION" ]; then
        echo "FATAL: bundled ensurepip wheel is $bundled but the recipe declares $PKG_VERSION — bump python-pip with the python upgrade" >&2
        exit 1
    fi

    # env -u DESTDIR: pip-backed installers redirect into an exported
    # DESTDIR (the known wheel-redirect class); the destination is passed
    # explicitly via --root instead.
    #
    # The bundled wheel is driven DIRECTLY, not via `-m ensurepip`:
    # ensurepip runs pip --isolated, so on a chroot whose live
    # site-packages already carries pip (the interpreter self-install
    # this package replaces) the resolver reports "already satisfied"
    # and writes NOTHING under --root — no env var can reach through the
    # isolation. --ignore-installed forces the staged deployment either
    # way. The bare `pip` script the wheel path emits is removed to keep
    # the payload identical to the self-install contract (pip3 + pip3.14
    # only, no bare alias).
    local dest="$DESTDIR"
    local wheel
    wheel=$(python3 -c 'import ensurepip, glob, os; print(glob.glob(os.path.join(os.path.dirname(ensurepip.__file__), "_bundled", "pip-*.whl"))[0])')
    env -u DESTDIR PYTHONPATH="$wheel" python3 -m pip install \
        --no-index --no-cache-dir --ignore-installed \
        --no-warn-script-location --root "$dest" "$wheel"
    rm -f "$dest/usr/bin/pip"

    # ensurepip resolves scripts against the interpreter prefix; assert
    # the payload landed where verify_paths pins it.
    if [ ! -x "$dest/usr/bin/pip3" ]; then
        echo "FATAL: ensurepip bootstrap produced no $dest/usr/bin/pip3" >&2
        exit 1
    fi
}
