#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2015-2016, 2026 InterGenJLU
#
# edk2-ovmf 202605 — OVMF UEFI firmware for QEMU guests
# Not in BLFS — InterGenOS extra tier (virtualization stack)
#
# TianoCore EDK II OVMF built from source at the edk2-stable202605 tag:
# Secure-Boot-capable (SECURE_BOOT_ENABLE + SMM_REQUIRE — variable
# store protected behind SMM, q35 machine type) with TPM 2.0 support.
# Ships UNENROLLED variables: Secure Boot is a capability the user
# arms with their own keys (or a distro's) — never forced on guests
# (decided 2026-07-16). Key enrollment happens per-VM via the firmware
# UI or virt-firmware tooling.
#
# Four of the five submodule tarballs are the exact commits recorded in
# the tag's .gitmodules (cross-checked against the upstream tree), each
# extracted explicitly below (multi-source rule): brotli lands in BOTH
# of its submodule paths. openssl is a DELIBERATE divergence from the
# recorded gitlink: the tag records the 3.5.1-prep commit; we build the
# 3.5.7 release tarball for its accumulated security fixes (latest
# stable in the 3.5 LTS line the gitlink sits on) — build-proven clean
# against edk2-stable202605 in this tree's proof builds (review finding F-02,
# 2026-07-17). The remaining submodules (cmocka, googletest,
# subhook, oniguruma, jansson, libfdt) serve unit tests / packages an
# X64 OVMF build does not reference and are not fetched.

EDK2_TAG_DIR=edk2-edk2-stable202605

extract_into() {
    # extract_into <tarball> <submodule-path> — strip the archive's top
    # dir and populate the (empty) submodule directory in-place.
    local tarball="$1" dest="$2"
    test -d "$dest"
    tar xf "$IGOS_SOURCES/$tarball" --strip-components=1 -C "$dest"
    test -n "$(ls -A "$dest")"   # fail loudly on an empty populate
}

configure() {
    set -e
    extract_into brotli-e230f474b87134e8c6c85b630084c612057f253e.tar.gz \
        BaseTools/Source/C/BrotliCompress/brotli
    extract_into brotli-e230f474b87134e8c6c85b630084c612057f253e.tar.gz \
        MdeModulePkg/Library/BrotliCustomDecompressLib/brotli
    extract_into openssl-3.5.7-edk2.tar.gz \
        CryptoPkg/Library/OpensslLib/openssl
    extract_into libspdm-1be116c7b7713fa9003e1bd53b53a34758549eb9.tar.gz \
        SecurityPkg/DeviceSecurity/SpdmLib/libspdm
    extract_into mbedtls-0bebf8b8c7f07abe3571ded48a11aa907a1ffb20.tar.gz \
        CryptoPkg/Library/MbedTlsLib/mbedtls
    extract_into mipi-sys-t-370b5944c046bab043dd8b133727b2135af7747a.tar.gz \
        MdePkg/Library/MipiSysTLib/mipisyst

    make -C BaseTools -j"$(nproc)"
}

build() {
    set -e
    export WORKSPACE="$PWD"
    export PYTHON_COMMAND=python3
    local jobs; jobs="$(nproc)"
    # edksetup.sh is written for sourcing in an interactive shell and
    # references unset vars; run it with the strict modes relaxed.
    set +e; source ./edksetup.sh BaseTools; set -e

    # ⚠ NEVER invoke the bare word `build` here: this phase function is
    # ITSELF named build(), so the bare word recurses into this function
    # instead of running edk2's build tool (infinite self-recursion,
    # env growth to E2BIG — bit the first proof build 2026-07-16).
    # Call the BinWrapper by absolute path.
    "$EDK_TOOLS_PATH/BinWrappers/PosixLike/build" \
        -a X64 -t GCC -b RELEASE -n "$jobs" \
        -p OvmfPkg/OvmfPkgX64.dsc \
        -D SECURE_BOOT_ENABLE=TRUE \
        -D SMM_REQUIRE=TRUE \
        -D TPM2_ENABLE=TRUE

    test -f Build/OvmfX64/RELEASE_GCC/FV/OVMF_CODE.fd
    test -f Build/OvmfX64/RELEASE_GCC/FV/OVMF_VARS.fd
}

do_install() {
    set -e
    install -Dm644 Build/OvmfX64/RELEASE_GCC/FV/OVMF_CODE.fd \
        "$DESTDIR/usr/share/edk2/x64/OVMF_CODE.fd"
    install -Dm644 Build/OvmfX64/RELEASE_GCC/FV/OVMF_VARS.fd \
        "$DESTDIR/usr/share/edk2/x64/OVMF_VARS.fd"

    # qemu firmware descriptor: lets libvirt/qemu auto-discover this
    # firmware for UEFI (and Secure-Boot-capable) x86_64 guests.
    install -Dm644 /dev/stdin \
        "$DESTDIR/usr/share/qemu/firmware/60-edk2-x86_64-secure.json" <<'JSON'
{
    "description": "OVMF (InterGenOS, from source), Secure Boot capable, unenrolled variables",
    "interface-types": [ "uefi" ],
    "mapping": {
        "device": "flash",
        "executable": {
            "filename": "/usr/share/edk2/x64/OVMF_CODE.fd",
            "format": "raw"
        },
        "nvram-template": {
            "filename": "/usr/share/edk2/x64/OVMF_VARS.fd",
            "format": "raw"
        }
    },
    "targets": [
        {
            "architecture": "x86_64",
            "machines": [ "pc-q35-*" ]
        }
    ],
    "features": [
        "acpi-s3",
        "requires-smm",
        "secure-boot",
        "verbose-dynamic"
    ],
    "tags": []
}
JSON
}
