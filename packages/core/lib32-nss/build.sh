#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# NSS 3.121 — 32-bit multilib runtime (GE arc, the sixth decided twin)
# Sibling: packages/core/nss (same tarball, same version — RT-9 lock).
# Profile: scripts/lib32-env.sh.
# Method: NSS's first-class build.sh gyp path with --target ia32
# (decided; provenance + the governed patch omission in
# package.yml). gyp is the in-tree gyp package (gyp-next).

source /mnt/intergenos/scripts/lib32-env.sh

configure() {
    set -e
    : # The gyp wrapper configures and builds in one step (build()).
}

build() {
    set -e
    cd nss
    # -v: verbose ninja through the wrapper — the archive-time time64
    # log assertion refuses a log with no visible compile evidence
    # (RT-8/F2-a). --target ia32 sets gyp target_arch, agreeing with
    # the profile's -m32 CC; --system-nspr/--system-sqlite resolve the
    # 32-bit nspr.pc/sqlite3.pc via the lib32 PKG_CONFIG_LIBDIR.
    bash ./build.sh -v -j ${IGOS_JOBS}  \
        --opt                           \
        --target ia32                   \
        --disable-tests                 \
        --enable-libpkix                \
        --system-nspr                   \
        --system-sqlite
}

do_install() {
    set -e
    # The gyp build lands everything under ../dist/Release (the --opt
    # target). Assemble the private root by hand (the wrapper has no
    # DESTDIR install), then allowlist-stage.
    local dist="dist/Release"
    [ -d "$dist/lib" ] || { echo "FATAL: $dist/lib missing — gyp build did not produce the Release tree" >&2; return 1; }

    install -dm755 m32root/usr/lib32
    install -m755 "$dist"/lib/*.so m32root/usr/lib32/
    # shlibsign integrity files (sibling parity — sign_libs defaults on;
    # their absence means the FIPS-integrity half silently vanished).
    # WC verify belt (2026-07-02, non-gating observation): count via find
    # instead of a bare-glob ls, so the guard never depends on the shell's
    # nullglob state (an option owned by other scripts, not this one).
    chk_count=$(find "$dist"/lib -maxdepth 1 -name '*.chk' -type f | wc -l)
    [ "$chk_count" -gt 0 ] || { echo "FATAL: no *.chk beside the built libs — shlibsign did not run (sign_libs flipped?)" >&2; return 1; }
    install -m644 "$dist"/lib/*.chk m32root/usr/lib32/

    # nss.pc — the same upstream-template sed as the sibling, with the
    # lib32 libdir (NSS's Makefiles never process the .pc.in themselves).
    install -dm755 m32root/usr/lib32/pkgconfig
    sed -e 's|%prefix%|/usr|g'                        \
        -e 's|%exec_prefix%|${prefix}|g'              \
        -e 's|%libdir%|${prefix}/lib32|g'             \
        -e 's|%includedir%|${prefix}/include/nss|g'   \
        -e "s|%NSS_VERSION%|${PKG_VERSION}|g"         \
        -e 's|%NSPR_VERSION%|4.38.2|g'                \
        nss/pkg/pkg-config/nss.pc.in                  \
        > m32root/usr/lib32/pkgconfig/nss.pc
    chmod 644 m32root/usr/lib32/pkgconfig/nss.pc

    # p11-kit trust module symlink (sibling parity) — resolves to
    # lib32-p11-kit's /usr/lib32/pkcs11/p11-kit-trust.so.
    ln -sfv ./pkcs11/p11-kit-trust.so m32root/usr/lib32/libnssckbi.so

    lib32_stage_libs "$PWD/m32root"
    lib32_assert_only_lib32
    lib32_env_end
}
