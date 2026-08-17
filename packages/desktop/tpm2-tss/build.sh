#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# tpm2-tss 4.1.3 — TCG TPM2 Software Stack
# Provides libtss2-{esys,sys,mu,rc,tcti-*} for systemd's TPM2 features
# (cryptenroll, pcrlock, measured-boot policies). Required by security
# design for the project's measured-boot stance — without it systemd
# silently disables all TPM2 features at configure time.
#
# Build #5 audit: not in package set → systemd-pass2 silently dropped tpm2.

configure() {
    set -e
    # Upstream tarball ships with autotools pre-bootstrapped (./configure
    # exists). No bootstrap step needed.
    # --localstatedir is REQUIRED: without it autoconf defaults to
    # ${prefix}/var, so the FAPI keystore/eventlog paths compile in as
    # /usr/var/lib + /usr/var/run, the generated tmpfiles.d conf carries
    # the same /usr/var paths, and the archive ships a bogus /usr/var
    # tree (state under read-only-/usr territory). Found 2026-07-17.
    ./configure --prefix=/usr                              \
                --localstatedir=/var                       \
                --disable-static                           \
                --with-udevrulesdir=/usr/lib/udev/rules.d  \
                --with-tmpfilesdir=/usr/lib/tmpfiles.d     \
                --with-sysusersdir=/usr/lib/sysusers.d     \
                --enable-fapi                              \
                --enable-policy
}

build() {
    set -e
    make -j${IGOS_JOBS}
}

check() {
    set -e
    pkg_run_tests "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/package.yml" \
        make check
}

do_install() {
    set -e
    make DESTDIR="$DESTDIR" install

    # Upstream's install target pre-creates the FAPI runtime eventlog dir
    # under localstatedir/run. Never ship var/run/ members: /var/run is a
    # symlink to /run on installed systems (base-files r9) and an archive
    # dir member would materialize it as a real dir at install time. The
    # upstream-generated tmpfiles.d/tpm2-tss-fapi.conf recreates it every
    # boot; the persistent /var/lib/tpm2-tss keystore stays in the archive.
    rm -rf "$DESTDIR/var/run"

    # The same localstatedir substitution puts /var/run into the generated
    # tmpfiles conf's two eventlog lines. /var/run is a compatibility symlink
    # to /run, and systemd-tmpfiles rewrites such a path on every parse while
    # logging that it did — once during the install and again at every boot:
    #   "Line references path below legacy directory /var/run/, updating
    #    /var/run/tpm2-tss/eventlog -> /run/tpm2-tss/eventlog"
    # Shipping the modern path removes the warning without changing where
    # anything lands (the two paths resolve to the same directory, so the
    # FAPI library's compiled-in /var/run path still finds it).
    #
    # Fail loud on an unexpected shape rather than no-op: a silent sed that
    # matched nothing would hide an upstream layout change on the next bump.
    _fapi_tmpfiles="${DESTDIR}/usr/lib/tmpfiles.d/tpm2-tss-fapi.conf"
    if [ ! -f "${_fapi_tmpfiles}" ]; then
        echo "FATAL: ${_fapi_tmpfiles} absent — upstream's install no longer generates the FAPI tmpfiles conf" >&2
        return 1
    fi
    if ! grep -q '/var/run/tpm2-tss/' "${_fapi_tmpfiles}"; then
        echo "FATAL: no /var/run/tpm2-tss/ entries in ${_fapi_tmpfiles} — the generated conf changed shape; re-derive this rewrite before shipping" >&2
        return 1
    fi
    sed -i 's|/var/run/tpm2-tss/|/run/tpm2-tss/|g' "${_fapi_tmpfiles}"
    if grep -q '/var/run/' "${_fapi_tmpfiles}"; then
        echo "FATAL: /var/run references remain in ${_fapi_tmpfiles} after the rewrite" >&2
        return 1
    fi
    unset _fapi_tmpfiles
}

post_install() {
    set -e
    # tpm2-tss-fapi.conf + tpm-udev.rules reference the `tss` system user/group.
    # tss user/group is declared by /usr/lib/sysusers.d/tpm2-tss.conf
    # and created by the pkm canonical sysusers hook before this
    # lifecycle hook runs.
    install -dm750 -o tss -g tss /var/lib/tpm 2>/dev/null || true
}
