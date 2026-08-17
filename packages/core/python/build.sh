#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# Python 3.14.3
# LFS 13.0 Section 8.52

configure() {
    set -e
    ./configure --prefix=/usr             \
        --enable-shared                   \
        --with-system-expat               \
        --with-system-libmpdec            \
        --enable-optimizations            \
        --without-static-libpython 2>&1 | tee configure-output.log
    local cfg_rc=${PIPESTATUS[0]}
    [ "$cfg_rc" -eq 0 ] || { echo "FATAL: python configure failed (rc=$cfg_rc)" >&2; exit 1; }

    # Make a silent libmpdec fallback LOUD. --with-system-libmpdec is the
    # default, but if pkg-config cannot find the system mpdecimal, configure
    # only WARNS and silently falls back to the bundled copy (deprecated,
    # upstream removal at 3.16). mpdecimal is a declared build dep built
    # earlier in ch8, so a fallback here means the dep chain broke — HALT.
    if grep -q "falling back to bundled libmpdec" configure-output.log; then
        echo "FATAL: python configure fell back to the bundled (deprecated) libmpdec — system mpdecimal was not detected despite being a declared build dep." >&2
        exit 1
    fi
}

build() {
    set -e
    # Exclude test_generators from PGO profiling — it fails under PGO
    # instrumentation in KVM/chroot due to signal delivery timing changes.
    # 1 of 46 PGO tests excluded; negligible impact on optimization quality.
    make PROFILE_TASK="-m test --pgo -x test_generators --timeout 120" \
        -j${IGOS_JOBS}
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # InterGenOS ships Tk-less (tkinter/_tkinter is not built — tk is not a
    # dependency), so IDLE (the Tkinter-based IDE) and its idlelib package are
    # dead weight that cannot import. Drop them per the documented Tk-less
    # posture; /usr/bin/idle3 is correspondingly removed from verify_paths.
    rm -fv "${DESTDIR}"/usr/bin/idle3*
    rm -rfv "${DESTDIR}/usr/lib/python3.14/idlelib"

    # PEP 394: 'python' should point to 'python3' on modern systems
    ln -sv python3 "${DESTDIR}/usr/bin/python"

    mkdir -pv "${DESTDIR}/etc"
    cat > "${DESTDIR}/etc/pip.conf" << PIPEOF
[install]
compile = no

[global]
root-user-action = ignore
disable-pip-version-check = true
break-system-packages = true

[freeze]
user = false
user-site = false
PIPEOF

    install -v -dm755 "${DESTDIR}/usr/share/doc/python-3.14.3/html"

    tar --no-same-owner \
        -xvf $IGOS_SOURCES/python-3.14.3-docs-html.tar.bz2
    cp -R --no-preserve=mode python-3.14.3-docs-html/* \
        "${DESTDIR}/usr/share/doc/python-3.14.3/html"
}
